#include "hal/uart.h"
#pragma pack(push, 1)
#include "tl_common.h"
#include "drivers/drv_uart.h"
#pragma pack(pop)

// TLSR8258 pin-function mux only routes the hardware UART to these pins
// (datasheet "GPIO lookup table" / SDK uart.h). Other pins can still transmit
// via bit-banging below.
static const uint16_t uart_hw_tx_pins[] = {
    GPIO_PA2, GPIO_PB1, GPIO_PC2, GPIO_PD0, GPIO_PD3, GPIO_PD7,
};
static const uint16_t uart_hw_rx_pins[] = {
    GPIO_PA0, GPIO_PB0, GPIO_PB7, GPIO_PC3, GPIO_PC5, GPIO_PD6,
};

// DMA RX buffer: first 4 bytes hold the received length, data follows.
static uint8_t uart_rx_dma_buf[72] __attribute__((aligned(4)));

static hal_uart_rx_callback_t uart_rx_cb = NULL;
static uint8_t        uart_tx_is_hw      = 0;
static hal_gpio_pin_t uart_tx_pin_g      = HAL_INVALID_PIN;
static uint32_t       uart_bit_ticks;         // sys-timer ticks per bit

static uint8_t pin_in_table(uint16_t pin, const uint16_t *table, uint8_t n) {
    for (uint8_t i = 0; i < n; i++) {
        if (table[i] == pin)
            return 1;
    }
    return 0;
}

// Called by the SDK irq dispatcher (via drv_uart) when an RX burst completed.
static void uart_on_rx_done(void) {
    uint32_t len = uart_rx_dma_buf[0] | (uart_rx_dma_buf[1] << 8) |
                   (uart_rx_dma_buf[2] << 16) | (uart_rx_dma_buf[3] << 24);

    if (len == 0 || len > sizeof(uart_rx_dma_buf) - 4)
        return;

    if (uart_rx_cb)
        uart_rx_cb(uart_rx_dma_buf + 4, (uint16_t)len);
}

int hal_uart_init(hal_gpio_pin_t tx_pin, hal_gpio_pin_t rx_pin,
                  uint32_t baudrate, hal_uart_rx_callback_t rx_cb) {
    if (baudrate == 0 ||
        !pin_in_table(rx_pin, uart_hw_rx_pins,
                      sizeof(uart_hw_rx_pins) / sizeof(uart_hw_rx_pins[0])))
        return -1;

    uart_rx_cb    = rx_cb;
    uart_tx_pin_g = tx_pin;
    // clock_time() reads the system timer (stimer), which on the TLSR825x
    // always ticks at 16 MHz (sys_tick_per_us = 16 in chip_8258/timer.h),
    // independent of the 24 MHz CPU clock. Deriving the bit period from
    // CLOCK_SYS_CLOCK_HZ instead skews the bit-banged baud rate by 24/16 and
    // the BL0942 never decodes the poll command.
    uart_bit_ticks = (sys_tick_per_us * 1000000u) / baudrate;
    uart_tx_is_hw  = pin_in_table(
        tx_pin, uart_hw_tx_pins,
        sizeof(uart_hw_tx_pins) / sizeof(uart_hw_tx_pins[0]));

    // Pin muxing first, then the UART block itself (mirrors the SDK's zbhci
    // usage: UART_PIN_CFG(); drv_uart_init(...)).
    if (uart_tx_is_hw) {
        drv_uart_pin_set(tx_pin, rx_pin);
    } else {
        // RX through the hardware UART, TX bit-banged as a plain GPIO.
        gpio_set_func((GPIO_PinTypeDef)rx_pin, AS_UART);
        gpio_set_input_en((GPIO_PinTypeDef)rx_pin, 1);
        gpio_set_output_en((GPIO_PinTypeDef)rx_pin, 0);
        gpio_setup_up_down_resistor((GPIO_PinTypeDef)rx_pin, PM_PIN_PULLUP_10K);

        gpio_set_func((GPIO_PinTypeDef)tx_pin, AS_GPIO);
        gpio_set_input_en((GPIO_PinTypeDef)tx_pin, 0);
        gpio_set_output_en((GPIO_PinTypeDef)tx_pin, 1);
        gpio_write((GPIO_PinTypeDef)tx_pin, 1); // UART idle level
    }

    if (drv_uart_init(baudrate, uart_rx_dma_buf, sizeof(uart_rx_dma_buf),
                      uart_on_rx_done) != 0)
        return -1;

    return 0;
}

// Wait until the system timer reaches `target` (handles wrap-around).
static inline void wait_until_tick(uint32_t target) {
    while ((uint32_t)(clock_time() - target) & 0x80000000u) {
    }
}

// Bit-banged 8N1 transmission. Interrupts are disabled for the duration
// (~2.1 ms per byte at 4800 baud) so the bit timing stays exact; callers only
// send a couple of poll bytes per second.
static void uart_bitbang_send(const uint8_t *data, uint8_t len) {
    GPIO_PinTypeDef pin = (GPIO_PinTypeDef)uart_tx_pin_g;

    uint8_t  r = irq_disable();
    uint32_t t = clock_time();

    for (uint8_t i = 0; i < len; i++) {
        uint16_t frame = 0x200 | ((uint16_t)data[i] << 1); // start(0),8 data,stop(1)
        for (uint8_t bit = 0; bit < 10; bit++) {
            gpio_write(pin, (frame >> bit) & 1);
            t += uart_bit_ticks;
            wait_until_tick(t);
        }
    }
    irq_restore(r);
}

void hal_uart_send(const uint8_t *data, uint8_t len) {
    if (len == 0 || uart_tx_pin_g == HAL_INVALID_PIN)
        return;

    if (uart_tx_is_hw) {
        drv_uart_tx_start((u8 *)data, len);
    } else {
        uart_bitbang_send(data, len);
    }
}

void hal_uart_task(void) {
    // Normally the platform irq_handler dispatches the UART RX DMA interrupt
    // and drv_uart calls our callback. Should that interrupt ever be masked or
    // lost, the DMA channel's status flag still latches — drain it here so
    // received bursts are delivered at poll cadence as a fallback.
    uint8_t r = irq_disable();

    if (dma_chn_irq_status_get() & FLD_DMA_CHN_UART_RX) {
        dma_chn_irq_status_clr(FLD_DMA_CHN_UART_RX);
        drv_uart_rx_irq_handler();
    }
    irq_restore(r);
}
