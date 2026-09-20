#include "bl0942.h"
#include "hal/timer.h"
#include "hal/uart.h"
#include "hal/printf_selector.h"
#include <string.h>

#define BL0942_READ_COMMAND     0x58
#define BL0942_FULL_PACKET      0xAA
#define BL0942_PACKET_HEADER    0x55

// The UART HAL has a single channel, so route its rx callback to the one
// driver instance (mirrors the g_elec_cluster pattern).
static bl0942_t *g_bl0942 = NULL;

static void bl0942_meter_get_data(void *ctx, energy_meter_data_t *data);
static void bl0942_meter_reset_energy(void *ctx);
static int  bl0942_meter_calibrate(void *ctx, energy_meter_channel_t channel,
                                   uint32_t reference);
static void bl0942_meter_get_calibration(void *ctx,
                                         energy_meter_calibration_t *cal);
static void bl0942_meter_set_calibration(void *ctx, uint32_t voltage_mult,
                                         uint32_t current_mult,
                                         uint32_t power_mult);

static const energy_meter_ops_t bl0942_energy_meter_ops = {
    .get_data          = bl0942_meter_get_data,
    .reset_energy      = bl0942_meter_reset_energy,
    .tick              = NULL,
    .calibrate         = bl0942_meter_calibrate,
    .get_calibration   = bl0942_meter_get_calibration,
    .set_calibration   = bl0942_meter_set_calibration,
    .get_instant_power = NULL, // get_data() power is already ~1 s fresh
};

// May run in interrupt context: only touch the rx ring.
void bl0942_rx_feed(bl0942_t *dev, const uint8_t *data, uint16_t len) {
    for (uint16_t i = 0; i < len; i++) {
        uint8_t next = (uint8_t)((dev->rx_head + 1) % BL0942_RX_RING_SIZE);
        if (next == dev->rx_tail)
            return; // ring full; excess is dropped and resynced by checksum

        dev->rx_ring[dev->rx_head] = data[i];
        dev->rx_head = next;
    }
}

static void bl0942_hal_rx_cb(const uint8_t *data, uint16_t len) {
    if (g_bl0942)
        bl0942_rx_feed(g_bl0942, data, len);
}

static uint8_t ring_used(bl0942_t *dev) {
    return (uint8_t)((dev->rx_head - dev->rx_tail + BL0942_RX_RING_SIZE) %
                     BL0942_RX_RING_SIZE);
}

static uint8_t ring_at(bl0942_t *dev, uint8_t offset) {
    return dev->rx_ring[(dev->rx_tail + offset) % BL0942_RX_RING_SIZE];
}

static uint32_t le24_at(bl0942_t *dev, uint8_t offset) {
    return (uint32_t)ring_at(dev, offset) |
           ((uint32_t)ring_at(dev, offset + 1) << 8) |
           ((uint32_t)ring_at(dev, offset + 2) << 16);
}

static void bl0942_apply_frame(bl0942_t *dev) {
    uint32_t i_rms = le24_at(dev, 1);
    uint32_t v_rms = le24_at(dev, 4);
    uint32_t watt  = le24_at(dev, 10);

    // WATT is signed 24-bit (negative when the load/line are swapped); the
    // magnitude is what we report.
    if (watt & 0x800000u)
        watt = (0x1000000u - watt) & 0xFFFFFFu;

    dev->data.raw_voltage = v_rms;
    dev->data.raw_current = i_rms;
    dev->data.raw_power   = watt;

    // Raw registers are 24-bit and field calibration is configurable. Use
    // 64-bit intermediates and saturate instead of wrapping a high reading
    // into an apparently safe low current/voltage/power.
    uint64_t voltage_scaled =
        (uint64_t)v_rms * dev->cal.voltage_multiplier /
        BL0942_FIXED_POINT_SCALE;
    uint64_t current_scaled =
        (uint64_t)i_rms * dev->cal.current_multiplier /
        BL0942_FIXED_POINT_SCALE;
    // Rounded to nearest whole watt so small loads read honestly (1.8 W -> 2).
    uint64_t power_scaled =
        ((uint64_t)watt * dev->cal.power_multiplier +
         BL0942_FIXED_POINT_SCALE / 2) / BL0942_FIXED_POINT_SCALE;
    dev->data.voltage = (uint16_t)(voltage_scaled > UINT16_MAX ?
                                   UINT16_MAX : voltage_scaled);
    dev->data.current = (uint16_t)(current_scaled > UINT16_MAX ?
                                   UINT16_MAX : current_scaled);
    dev->data.power = (int16_t)(power_scaled > INT16_MAX ?
                                INT16_MAX : power_scaled);

    // Use the BL0942's hardware CF energy counter rather than integrating
    // rounded whole-watt readings.  First frame only establishes a baseline.
    uint32_t cf_count = le24_at(dev, 13) & BL0942_CF_COUNTER_MASK;
    if (dev->data.cf_baseline_valid) {
        uint32_t delta_cf = 0;
        if (cf_count >= dev->data.last_cf_count) {
            delta_cf = cf_count - dev->data.last_cf_count;
        } else if (dev->data.last_cf_count >= 0x00FF0000u &&
                   cf_count <= 0x0000FFFFu) {
            // Natural 24-bit wrap between adjacent polls.
            delta_cf = (BL0942_CF_COUNTER_MASK + 1u -
                        dev->data.last_cf_count) + cf_count;
        } else {
            // Backwards jump away from the wrap boundary means the metering
            // IC reset/restarted. Re-baseline without manufacturing energy.
            delta_cf = 0;
        }

        uint64_t scaled = (uint64_t)dev->data.cf_remainder +
                          (uint64_t)delta_cf *
                          (uint64_t)dev->cal.power_multiplier * 8u;
        uint64_t whole_wh = scaled / BL0942_CF_ENERGY_DENOMINATOR;
        if (whole_wh >= UINT32_MAX - dev->data.energy) {
            // Preserve monotonicity rather than wrapping cumulative energy.
            dev->data.energy       = UINT32_MAX;
            dev->data.cf_remainder = 0;
        } else {
            dev->data.energy      += (uint32_t)whole_wh;
            dev->data.cf_remainder =
                (uint32_t)(scaled % BL0942_CF_ENERGY_DENOMINATOR);
        }
    } else {
        dev->data.cf_baseline_valid = 1;
    }
    dev->data.last_cf_count       = cf_count;
    dev->data.valid               = 1;
    dev->data.last_valid_frame_ms = hal_millis();
}

// Scan the rx ring for complete, checksum-valid frames.
void bl0942_process_rx(bl0942_t *dev) {
    while (ring_used(dev) >= BL0942_FRAME_LEN) {
        if (ring_at(dev, 0) != BL0942_PACKET_HEADER) {
            dev->rx_tail = (uint8_t)((dev->rx_tail + 1) % BL0942_RX_RING_SIZE);
            continue;
        }

        uint8_t checksum = BL0942_READ_COMMAND;
        for (uint8_t i = 0; i < BL0942_FRAME_LEN - 1; i++) {
            checksum = (uint8_t)(checksum + ring_at(dev, i));
        }
        checksum ^= 0xFF;

        if (checksum != ring_at(dev, BL0942_FRAME_LEN - 1)) {
            // Bad frame or mid-stream sync: skip this header byte and rescan.
            dev->rx_tail = (uint8_t)((dev->rx_tail + 1) % BL0942_RX_RING_SIZE);
            continue;
        }

        bl0942_apply_frame(dev);
        dev->rx_tail = (uint8_t)((dev->rx_tail + BL0942_FRAME_LEN) %
                                 BL0942_RX_RING_SIZE);
    }
}

static void bl0942_poll_handler(void *arg) {
    bl0942_t *dev = (bl0942_t *)arg;

    // Drain any reception the RX interrupt may have missed, then parse.
    hal_uart_task();
    bl0942_process_rx(dev);

    static const uint8_t poll_cmd[2] = { BL0942_READ_COMMAND, BL0942_FULL_PACKET };
    hal_uart_send(poll_cmd, sizeof(poll_cmd));

    hal_tasks_schedule(&dev->poll_task, BL0942_POLL_INTERVAL_MS);
}

int bl0942_init(bl0942_t *dev, hal_gpio_pin_t tx_pin, hal_gpio_pin_t rx_pin,
                uint32_t baudrate) {
    if (!dev)
        return -1;

    memset(dev, 0, sizeof(bl0942_t));
    dev->tx_pin = tx_pin;
    dev->rx_pin = rx_pin;

    dev->cal.voltage_multiplier = BL0942_VOLTAGE_MULTIPLIER;
    dev->cal.current_multiplier = BL0942_CURRENT_MULTIPLIER;
    dev->cal.power_multiplier   = BL0942_POWER_MULTIPLIER;

    g_bl0942 = dev;
    if (hal_uart_init(tx_pin, rx_pin, baudrate, bl0942_hal_rx_cb) != 0) {
        g_bl0942 = NULL;
        printf("BL0942: UART init failed (TX=%04x RX=%04x)\r\n", tx_pin, rx_pin);
        return -1;
    }

    dev->initialized = 1;

    energy_meter_init(&dev->meter, &bl0942_energy_meter_ops, dev,
                      ENERGY_METER_BL0942);

    dev->poll_task.handler = bl0942_poll_handler;
    dev->poll_task.arg     = dev;
    hal_tasks_init(&dev->poll_task);
    hal_tasks_schedule(&dev->poll_task, BL0942_POLL_INTERVAL_MS);

    printf("BL0942: Initialized on TX=%04x RX=%04x baud %u\r\n", tx_pin, rx_pin,
           (unsigned int)baudrate);
    return 0;
}

void bl0942_set_calibration(bl0942_t *dev, uint32_t voltage_mult,
                            uint32_t current_mult, uint32_t power_mult) {
    if (!dev)
        return;

    // Zero means "keep the current value".
    if (voltage_mult)
        dev->cal.voltage_multiplier = voltage_mult;
    if (current_mult)
        dev->cal.current_multiplier = current_mult;
    if (power_mult && power_mult <= BL0942_MAX_POWER_MULTIPLIER)
        dev->cal.power_multiplier = power_mult;

    printf("BL0942: calibration V=%u A=%u W=%u\r\n",
           (unsigned int)dev->cal.voltage_multiplier,
           (unsigned int)dev->cal.current_multiplier,
           (unsigned int)dev->cal.power_multiplier);
}

energy_meter_t *bl0942_as_energy_meter(bl0942_t *dev) {
    return dev ? &dev->meter : NULL;
}

static void bl0942_meter_get_data(void *ctx, energy_meter_data_t *data) {
    bl0942_t *dev = (bl0942_t *)ctx;

    memset(data, 0, sizeof(*data));
    // A checksum-valid frame is required recently; retaining the last
    // measurement as valid indefinitely could conceal a failed meter.
    data->energy = dev->data.energy;
    if (!dev->data.valid ||
        (uint32_t)(hal_millis() - dev->data.last_valid_frame_ms) >
        BL0942_STALE_AFTER_MS) {
        data->valid = 0;
        return;
    }
    data->voltage = dev->data.voltage;
    data->current = dev->data.current;
    data->power   = dev->data.power;
    data->valid   = 1;
}

static void bl0942_meter_reset_energy(void *ctx) {
    bl0942_t *dev = (bl0942_t *)ctx;

    dev->data.energy            = 0;
    dev->data.cf_remainder      = 0;
    dev->data.cf_baseline_valid = 0;
}

static int bl0942_meter_calibrate(void *ctx, energy_meter_channel_t channel,
                                  uint32_t reference) {
    bl0942_t *dev = (bl0942_t *)ctx;

    if (!dev || reference == 0)
        return -1;

    uint32_t  raw;
    uint32_t *target;
    switch (channel) {
    case ENERGY_METER_CHANNEL_VOLTAGE:
        raw    = dev->data.raw_voltage;
        target = &dev->cal.voltage_multiplier;
        break;
    case ENERGY_METER_CHANNEL_CURRENT:
        raw    = dev->data.raw_current;
        target = &dev->cal.current_multiplier;
        break;
    case ENERGY_METER_CHANNEL_POWER:
        raw    = dev->data.raw_power;
        target = &dev->cal.power_multiplier;
        break;
    default:
        return -1;
    }

    // Calibration requires fresh, nonzero sensor data; never learn from a
    // disconnected meter or an arbitrarily old reading.
    if (!dev->data.valid ||
        (uint32_t)(hal_millis() - dev->data.last_valid_frame_ms) >
        BL0942_STALE_AFTER_MS || raw == 0)
        return -1;

    // The public calibration API uses uint32 reference. Avoid a 32-bit
    // intermediate overflow, and reject rather than truncate invalid gains.
    uint64_t proposed =
        ((uint64_t)reference * BL0942_FIXED_POINT_SCALE) / raw;
    uint32_t max_gain = channel == ENERGY_METER_CHANNEL_POWER ?
                        BL0942_MAX_POWER_MULTIPLIER : UINT32_MAX;
    if (proposed == 0 || proposed > max_gain)
        return -1;

    *target = (uint32_t)proposed;

    printf("BL0942: calibrated ch %u to ref %u (raw %u) => mult %u\r\n",
           (unsigned int)channel, (unsigned int)reference,
           (unsigned int)raw, (unsigned int)*target);
    return 0;
}

static void bl0942_meter_get_calibration(void *ctx,
                                         energy_meter_calibration_t *cal) {
    bl0942_t *dev = (bl0942_t *)ctx;

    cal->voltage_multiplier = dev->cal.voltage_multiplier;
    cal->current_multiplier = dev->cal.current_multiplier;
    cal->power_multiplier   = dev->cal.power_multiplier;
}

static void bl0942_meter_set_calibration(void *ctx, uint32_t voltage_mult,
                                         uint32_t current_mult,
                                         uint32_t power_mult) {
    bl0942_set_calibration((bl0942_t *)ctx, voltage_mult, current_mult,
                           power_mult);
}
