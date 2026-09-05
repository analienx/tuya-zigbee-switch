#include "hal/gpio.h"
#include "hal/printf_selector.h"
#include "hal/zigbee.h"
#include "zigbee/basic_cluster.h"
#include "zigbee/battery_cluster.h"
#include "zigbee/consts.h"
#include "zigbee/cover_cluster.h"
#include "zigbee/cover_switch_cluster.h"
#include "zigbee/group_cluster.h"
#include "zigbee/relay_cluster.h"
#include "zigbee/poll_control_cluster.h"
#include "zigbee/switch_cluster.h"
#include "zigbee/electrical_measurement_cluster.h"
#include "zigbee/metering_cluster.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "base_components/led.h"
#include "base_components/network_indicator.h"
#include "base_components/battery.h"
#include "base_components/energy_measurement/hlw8012.h"
#include "config_nv.h"
#include "device_config/device_params_nv.h"
#include "device_config/reset.h"
#include "hal/system.h"
#include "hal/zigbee.h"
#include "hal/zigbee_ota.h"

void peripherals_init(void);

network_indicator_t network_indicator = {
    .leds                        = { NULL, NULL, NULL, NULL },
    .has_dedicated_led           = 0,
    .manual_state_when_connected = 1,
};

led_t   leds[5];
uint8_t leds_cnt = 0;

button_t buttons[11];
uint8_t  buttons_cnt = 0;

relay_t relays[10]; // 4 relay endpoints + 3 cover endpoints
uint8_t relays_cnt = 0;

zigbee_basic_cluster basic_cluster = {
    .deviceEnable = 1,
};

zigbee_group_cluster group_cluster = {};

zigbee_switch_cluster switch_clusters[4];
uint8_t switch_clusters_cnt = 0;

zigbee_relay_cluster relay_clusters[4];
uint8_t relay_clusters_cnt = 0;

zigbee_cover_switch_cluster cover_switch_clusters[3];
uint8_t cover_switch_clusters_cnt = 0;

zigbee_cover_cluster cover_clusters[3];
uint8_t cover_clusters_cnt = 0;

hal_zigbee_cluster  clusters[32];
hal_zigbee_endpoint endpoints[10];

uint8_t allow_simultaneous_latching_pulses = 0;

battery_t battery = {
    .pin         = HAL_INVALID_PIN,
    .voltage_min =            2000,
    .voltage_max =            3000,
};

static hlw8012_t       hlw8012_device;
static energy_meter_t *energy_meter = NULL;
static electrical_measurement_cluster_t elec_meas_cluster;
static metering_cluster_t metering_cluster_inst;
static uint8_t            energy_monitoring_enabled       = 0;
static uint8_t            energy_monitoring_endpoint      = 1;
static uint8_t            energy_monitoring_protect_relay = 0;

uint32_t parse_int(const char *s);
char *seek_until(char *cursor, char needle);
char *extract_next_entry(char **cursor);

static bool init_hlw8012_energy_meter(hal_gpio_pin_t cf_pin,
                                      hal_gpio_pin_t cf1_pin,
                                      hal_gpio_pin_t sel_pin,
                                      uint8_t sel_inverted,
                                      uint32_t voltage_mult,
                                      uint32_t current_mult,
                                      uint32_t power_mult) {
    if (energy_monitoring_enabled) {
        printf("Config: refusing duplicate energy meter\r\n");
        return false;
    }

    if (hlw8012_init(&hlw8012_device, cf_pin, cf1_pin, sel_pin) != 0) {
        printf("Config: failed to initialize HLW8012/BL0937 meter\r\n");
        return false;
    }

    hlw8012_set_sel_inverted(&hlw8012_device, sel_inverted);
    hlw8012_set_calibration(&hlw8012_device, voltage_mult, current_mult,
                            power_mult);
    energy_meter = hlw8012_as_energy_meter(&hlw8012_device);
    if (!energy_meter) {
        printf("Config: energy meter adapter unavailable\r\n");
        return false;
    }

    electrical_measurement_cluster_init(&elec_meas_cluster, energy_meter);
    metering_cluster_init(&metering_cluster_inst, energy_meter);
    energy_monitoring_enabled  = 1;
    energy_monitoring_endpoint = 1;
    return true;
}

void on_reset_clicked(void *_) {
    hal_factory_reset();
}

void on_multi_press_reset(void *_, uint8_t press_count) {
    if (g_multi_press_reset_count != 0 &&
        press_count >= g_multi_press_reset_count) {
        hal_factory_reset();
    }
}

void parse_config() {
    device_config_read_from_nv();

    /* Existing converted BSEED PM sockets deliberately persist the short
     * production config without an EP token. Detect an explicit EP before the
     * parser temporarily NUL-terminates tokens so an explicit future config
     * always wins over the board compatibility fallback. */
    bool has_explicit_energy_token =
        strstr((const char *)device_config_str.data, ";EP") != NULL;

    char *      cursor          = (char *)device_config_str.data;
    const char *zb_manufacturer = extract_next_entry(&cursor);

    basic_cluster.manuName[0] = strlen(zb_manufacturer);
    if (basic_cluster.manuName[0] > 31) {
        printf("Manufacturer too big\r\n");
        reset_all();
    }
    memcpy(basic_cluster.manuName + 1, zb_manufacturer,
           basic_cluster.manuName[0]);

    const char *zb_model = extract_next_entry(&cursor);
    basic_cluster.modelId[0] = strlen(zb_model);
    if (basic_cluster.modelId[0] > 31) {
        printf("Model too big\r\n");
        reset_all();
    }
    memcpy(basic_cluster.modelId + 1, zb_model, basic_cluster.modelId[0]);

#ifdef BSEED_PM_B28WRPVX
    /* Hardware-proven compatibility path for already-converted sockets. It
    * changes no NVM config bytes and is compiled only into the dedicated PM
    * target. The production campaign proved CF=A1, CF1=C2, SEL=B1 and the
    * non-inverted selector interpretation used by the accepted overlay. */
    if (!has_explicit_energy_token && strcmp(zb_manufacturer, "b28wrpvx") == 0 &&
        strcmp(zb_model, "TS011F-BS-PM") == 0) {
        hal_gpio_pin_t cf_pin  = hal_gpio_parse_pin("A1");
        hal_gpio_pin_t cf1_pin = hal_gpio_parse_pin("C2");
        hal_gpio_pin_t sel_pin = hal_gpio_parse_pin("B1");
        if (init_hlw8012_energy_meter(cf_pin, cf1_pin, sel_pin, 0, 0, 0, 0)) {
            printf("Config: implicit b28wrpvx BL0937 meter CF=A1 CF1=C2 SEL=B1\r\n");
#ifdef BSEED_PM_B28WRPVX_PROTECTION
            energy_monitoring_protect_relay = 1;
#endif
        }
    }
#endif

    bool     has_dedicated_status_led = false;
    uint16_t debounce_ms = DEBOUNCE_DELAY_MS;
    char *   entry;
    for (entry = extract_next_entry(&cursor); *entry != '\0';
         entry = extract_next_entry(&cursor)) {
        if (entry[0] == 'S' && entry[1] == 'L' && entry[2] == 'P') {
            allow_simultaneous_latching_pulses = 1;
        } else if (entry[0] == 'D' && entry[1] >= '0' && entry[1] <= '9') {
            debounce_ms = (uint16_t)parse_int(entry + 1);
            for (int i = 0; i < buttons_cnt; i++) {
                buttons[i].debounce_delay_ms = debounce_ms;
            }
        } else if (entry[0] == 'B' && entry[1] == 'T') {
            hal_gpio_pin_t pin = hal_gpio_parse_pin(entry + 2);
            battery.pin = pin;
            battery_init(&battery);
        } else if (entry[0] == 'B') {
            hal_gpio_pin_t  pin  = hal_gpio_parse_pin(entry + 1);
            hal_gpio_pull_t pull = hal_gpio_parse_pull(entry + 3);
            hal_gpio_init(pin, 1, pull);

            buttons[buttons_cnt].pin = pin;
            buttons[buttons_cnt].long_press_duration_ms  = 2000;
            buttons[buttons_cnt].multi_press_duration_ms = 800;
            buttons[buttons_cnt].debounce_delay_ms       = debounce_ms;
            buttons[buttons_cnt].on_long_press           = on_reset_clicked;
            buttons_cnt++;
        } else if (entry[0] == 'L') {
            hal_gpio_pin_t pin = hal_gpio_parse_pin(entry + 1);
            hal_gpio_init(pin, 0, HAL_GPIO_PULL_NONE);
            leds[leds_cnt].pin     = pin;
            leds[leds_cnt].on_high = entry[3] != 'i';

            led_init(&leds[leds_cnt]);

            network_indicator.leds[0]           = &leds[leds_cnt];
            network_indicator.leds[1]           = NULL;
            network_indicator.has_dedicated_led = true;

            has_dedicated_status_led = true;
            leds_cnt++;
        } else if (entry[0] == 'I') {
            hal_gpio_pin_t pin = hal_gpio_parse_pin(entry + 1);
            hal_gpio_init(pin, 0, HAL_GPIO_PULL_NONE);
            leds[leds_cnt].pin     = pin;
            leds[leds_cnt].on_high = entry[3] != 'i';
            led_init(&leds[leds_cnt]);

            for (int index = 0; index < 4; index++) {
                if (relay_clusters[index].indicator_led == NULL) {
                    relay_clusters[index].indicator_led = &leds[leds_cnt];
                    break;
                }
            }

            for (int index = 0; index < 4; index++) {
                if (switch_clusters[index].indicator_led == NULL) {
                    switch_clusters[index].indicator_led = &leds[leds_cnt];
                    break;
                }
            }

            if (!has_dedicated_status_led) {
                for (int index = 0; index < 4; index++) {
                    if (network_indicator.leds[index] == NULL) {
                        network_indicator.leds[index] = &leds[leds_cnt];
                        break;
                    }
                }
            }
            leds_cnt++;
        } else if (entry[0] == 'S') {
            hal_gpio_pin_t  pin  = hal_gpio_parse_pin(entry + 1);
            hal_gpio_pull_t pull = hal_gpio_parse_pull(entry + 3);
            hal_gpio_init(pin, 1, pull);

            buttons[buttons_cnt].pin = pin;
            buttons[buttons_cnt].long_press_duration_ms  = 800;
            buttons[buttons_cnt].multi_press_duration_ms = 800;
            buttons[buttons_cnt].debounce_delay_ms       = debounce_ms;
            buttons[buttons_cnt].on_multi_press          = on_multi_press_reset;

            if (entry[3] == 'd')
                buttons[buttons_cnt].pressed_when_high = 1;
            switch_clusters[switch_clusters_cnt].switch_idx = switch_clusters_cnt;
            switch_clusters[switch_clusters_cnt].mode       =
                ZCL_ONOFF_CONFIGURATION_SWITCH_TYPE_TOGGLE;
            switch_clusters[switch_clusters_cnt].action =
                ZCL_ONOFF_CONFIGURATION_SWITCH_ACTION_TOGGLE_SIMPLE;
            switch_clusters[switch_clusters_cnt].relay_mode =
                ZCL_ONOFF_CONFIGURATION_RELAY_MODE_SHORT;
            switch_clusters[switch_clusters_cnt].binded_mode =
                ZCL_ONOFF_CONFIGURATION_BINDED_MODE_SHORT;
            switch_clusters[switch_clusters_cnt].relay_index =
                switch_clusters_cnt + 1;
            switch_clusters[switch_clusters_cnt].button          = &buttons[buttons_cnt];
            switch_clusters[switch_clusters_cnt].level_move_rate = 50;
            buttons_cnt++;
            switch_clusters_cnt++;
        } else if (entry[0] == 'R') {
            hal_gpio_pin_t pin = hal_gpio_parse_pin(entry + 1);
            // Relay outputs are enabled only by relay_cluster_add_to_endpoint()
            // after the persisted physical policy is loaded.
            relays[relays_cnt].pin     = pin;
            relays[relays_cnt].on_high = 1;

            if (entry[3] != '\0') {
                pin = hal_gpio_parse_pin(entry + 3);
                relays[relays_cnt].off_pin     = pin;
                relays[relays_cnt].is_latching = 1;
            }

            relay_clusters[relay_clusters_cnt].relay_idx = relay_clusters_cnt;
            relay_clusters[relay_clusters_cnt].relay     = &relays[relays_cnt];
            relays_cnt++;
            relay_clusters_cnt++;
        } else if (entry[0] == 'X') {
            hal_gpio_pin_t  open_pin  = hal_gpio_parse_pin(entry + 1);
            hal_gpio_pin_t  close_pin = hal_gpio_parse_pin(entry + 3);
            hal_gpio_pull_t pull      = hal_gpio_parse_pull(entry + 5);

            hal_gpio_init(open_pin, 1, pull);
            hal_gpio_init(close_pin, 1, pull);

            buttons[buttons_cnt].pin = open_pin;
            buttons[buttons_cnt].long_press_duration_ms  = 800;
            buttons[buttons_cnt].multi_press_duration_ms = 800;
            buttons[buttons_cnt].debounce_delay_ms       = debounce_ms;
            buttons[buttons_cnt].on_multi_press          = on_multi_press_reset;
            button_t *open_button = &buttons[buttons_cnt++];

            buttons[buttons_cnt].pin = close_pin;
            buttons[buttons_cnt].long_press_duration_ms  = 800;
            buttons[buttons_cnt].multi_press_duration_ms = 800;
            buttons[buttons_cnt].debounce_delay_ms       = debounce_ms;
            buttons[buttons_cnt].on_multi_press          = on_multi_press_reset;
            button_t *close_button = &buttons[buttons_cnt++];

            cover_switch_clusters[cover_switch_clusters_cnt].open_button =
                open_button;
            cover_switch_clusters[cover_switch_clusters_cnt].close_button =
                close_button;
            cover_switch_clusters[cover_switch_clusters_cnt].cover_switch_idx =
                cover_switch_clusters_cnt;
            cover_switch_clusters_cnt++;
        } else if (entry[0] == 'C') {
            hal_gpio_pin_t open_pin  = hal_gpio_parse_pin(entry + 1);
            hal_gpio_pin_t close_pin = hal_gpio_parse_pin(entry + 3);

            relays[relays_cnt].pin         = open_pin;
            relays[relays_cnt].on_high     = 1;
            relays[relays_cnt].is_latching = 0;
            relay_t *open_relay = &relays[relays_cnt++];

            relays[relays_cnt].pin         = close_pin;
            relays[relays_cnt].on_high     = 1;
            relays[relays_cnt].is_latching = 0;
            relay_t *close_relay = &relays[relays_cnt++];

            cover_clusters[cover_clusters_cnt].open_relay  = open_relay;
            cover_clusters[cover_clusters_cnt].close_relay = close_relay;
            cover_clusters[cover_clusters_cnt].cover_idx   = cover_clusters_cnt;
            cover_clusters_cnt++;
        } else if (entry[0] == 'i') {
            uint32_t image_type = parse_int(entry + 1);
            hal_zigbee_set_image_type(image_type);
        } else if (entry[0] == 'M') {
            for (int index = 0; index < switch_clusters_cnt; index++) {
                switch_clusters[index].mode =
                    ZCL_ONOFF_CONFIGURATION_SWITCH_TYPE_MOMENTARY;
            }
        } else if (entry[0] == 'E' && entry[1] == 'P') {
            hal_gpio_pin_t cf_pin   = hal_gpio_parse_pin(entry + 2);
            hal_gpio_pin_t cf1_pin  = hal_gpio_parse_pin(entry + 4);
            hal_gpio_pin_t sel_pin  = hal_gpio_parse_pin(entry + 6);
            const char *   cal      = entry + 8;
            const char *   v        = seek_until((char *)cal, 'V');
            const char *   a        = seek_until((char *)cal, 'A');
            const char *   w        = seek_until((char *)cal, 'W');
            uint8_t        inverted =
                *seek_until((char *)cal, 'I') == 'I' ? 1 : 0;

            if (init_hlw8012_energy_meter(
                    cf_pin, cf1_pin, sel_pin, inverted,
                    (*v == 'V') ? parse_int(v + 1) : 0,
                    (*a == 'A') ? parse_int(a + 1) : 0,
                    (*w == 'W') ? parse_int(w + 1) : 0)) {
                printf("Config: explicit pulse meter CF=%04x CF1=%04x SEL=%04x\r\n",
                       cf_pin, cf1_pin, sel_pin);
            }
        } else if (entry[0] == 'O' && entry[1] == 'L') {
            if (energy_monitoring_enabled) {
                const char *c = seek_until(entry + 2, 'C');
                const char *p = seek_until(entry + 2, 'P');
                overload_protection_set_current_limits(
                    &elec_meas_cluster.overload,
                    (*c == 'C') ? (uint16_t)parse_int(c + 1) : 0,
                    (*p == 'P') ? (uint16_t)parse_int(p + 1) : 0);
                energy_monitoring_protect_relay = 1;
            }
        }
    }

    peripherals_init();

    printf("Initializing Zigbee with %d switches, %d relays, %d cover switches, "
           "%d covers%s\r\n",
           switch_clusters_cnt, relay_clusters_cnt, cover_switch_clusters_cnt,
           cover_clusters_cnt,
           energy_monitoring_enabled ? ", metering" : "");

    uint8_t total_endpoints = switch_clusters_cnt + relay_clusters_cnt +
                              cover_switch_clusters_cnt + cover_clusters_cnt;

    hal_zigbee_cluster *cluster_ptr = clusters;

    for (int index = 0; index < switch_clusters_cnt; index++) {
        if (switch_clusters[index].relay_index > relay_clusters_cnt) {
            switch_clusters[index].relay_mode =
                ZCL_ONOFF_CONFIGURATION_RELAY_MODE_DETACHED;
            switch_clusters[index].relay_index = 0;
        }
    }

    if (total_endpoints == 0)
        total_endpoints = 1;

    for (int index = 0; index < total_endpoints; index++) {
        endpoints[index].endpoint   = index + 1;
        endpoints[index].profile_id = 0x0104;
        endpoints[index].device_id  = 0xffff;
    }

    endpoints[0].clusters = cluster_ptr;
    basic_cluster_add_to_endpoint(&basic_cluster, &endpoints[0]);

    hal_ota_cluster_setup(&endpoints[0].clusters[endpoints[0].cluster_count]);
    endpoints[0].cluster_count++;

    if (battery.pin != HAL_INVALID_PIN) {
        static zigbee_battery_cluster battery_cluster;
        battery_cluster_add_to_endpoint(&battery_cluster, &endpoints[0]);
    }

#ifdef END_DEVICE
    static zigbee_poll_control_cluster poll_ctrl_cluster;
    poll_control_cluster_add_to_endpoint(&poll_ctrl_cluster, &endpoints[0],
                                         battery.pin != HAL_INVALID_PIN);
#endif

    for (int index = 0; index < switch_clusters_cnt; index++) {
        if (index != 0) {
            cluster_ptr += endpoints[index - 1].cluster_count;
            endpoints[index].clusters = cluster_ptr;
        }
        switch_cluster_add_to_endpoint(&switch_clusters[index], &endpoints[index]);
    }

    /* Metering belongs to the configured endpoint (BSEED uses EP1). Add it
     * before the relay loop so subsequent cluster_ptr arithmetic sees EP1's
     * final cluster count. V8 preflight already reserved both cluster slots. */
    if (energy_monitoring_enabled && energy_monitoring_endpoint == 1) {
        electrical_measurement_cluster_add_to_endpoint(&elec_meas_cluster,
                                                       &endpoints[0]);
        metering_cluster_add_to_endpoint(&metering_cluster_inst, &endpoints[0]);
    }

    for (int index = 0; index < relay_clusters_cnt; index++) {
        if (switch_clusters_cnt + index != 0) {
            cluster_ptr += endpoints[switch_clusters_cnt + index - 1].cluster_count;
            endpoints[switch_clusters_cnt + index].clusters = cluster_ptr;
        }
        relay_cluster_add_to_endpoint(&relay_clusters[index],
                                      &endpoints[switch_clusters_cnt + index]);
        group_cluster_add_to_endpoint(&group_cluster,
                                      &endpoints[switch_clusters_cnt + index]);
    }

    if (energy_monitoring_enabled && energy_monitoring_protect_relay &&
        relay_clusters_cnt > 0) {
        electrical_measurement_cluster_set_protected_relay(&elec_meas_cluster,
                                                           &relay_clusters[0]);
    }

    int cover_switch_base = switch_clusters_cnt + relay_clusters_cnt;
    for (int index = 0; index < cover_switch_clusters_cnt; index++) {
        if (cover_switch_base + index != 0) {
            cluster_ptr += endpoints[cover_switch_base + index - 1].cluster_count;
            endpoints[cover_switch_base + index].clusters = cluster_ptr;
        }
        cover_switch_cluster_add_to_endpoint(&cover_switch_clusters[index],
                                             &endpoints[cover_switch_base + index]);
    }

    int cover_base =
        switch_clusters_cnt + relay_clusters_cnt + cover_switch_clusters_cnt;
    for (int index = 0; index < cover_clusters_cnt; index++) {
        if (cover_base + index != 0) {
            cluster_ptr += endpoints[cover_base + index - 1].cluster_count;
            endpoints[cover_base + index].clusters = cluster_ptr;
        }
        cover_cluster_add_to_endpoint(&cover_clusters[index],
                                      &endpoints[cover_base + index]);
    }

    hal_zigbee_init(endpoints, total_endpoints);
    while (cursor != (char *)device_config_str.data) {
        cursor--;
        if (*cursor == '\0')
            *cursor = ';';
    }

    printf("Config parsed successfully\r\n");
}

void network_indicator_on_network_status_change(
    hal_zigbee_network_status_t new_status) {
    printf("Network status changed to %d\r\n", new_status);
    if (new_status == HAL_ZIGBEE_NETWORK_JOINED) {
        if (battery.pin != HAL_INVALID_PIN)
            network_indicator.manual_state_when_connected = 0;
        network_indicator_connected(&network_indicator);
        update_switch_clusters();
        update_relay_clusters();
    } else {
        network_indicator_not_connected(&network_indicator);
    }
}

void peripherals_init() {
    for (int index = 0; index < buttons_cnt; index++)
        btn_init(&buttons[index]);
    for (int index = 0; index < leds_cnt; index++)
        led_init(&leds[index]);

    // Relay GPIOs remain deferred to relay_cluster_add_to_endpoint().
    if (hal_zigbee_get_network_status() == HAL_ZIGBEE_NETWORK_JOINED) {
        network_indicator_connected(&network_indicator);
        update_switch_clusters();
        update_relay_clusters();
    } else {
        network_indicator_not_connected(&network_indicator);
    }
    hal_register_on_network_status_change_callback(
        network_indicator_on_network_status_change);
}

char *seek_until(char *cursor, char needle) {
    while (*cursor != needle && *cursor != '\0')
        cursor++;
    return(cursor);
}

char *extract_next_entry(char **cursor) {
    char *end = seek_until(*cursor, ';');

    *end = '\0';
    char *res = *cursor;
    *cursor = end + 1;
    return(res);
}

uint32_t parse_int(const char *s) {
    if (!s)
        return 0;

    uint32_t n = 0;
    while (*s >= '0' && *s <= '9') {
        n = n * 10 + (uint32_t)(*s - '0');
        s++;
    }
    return n;
}

void init_energy_reporting(void) {
    if (!energy_monitoring_enabled)
        return;

    electrical_measurement_cluster_report(&elec_meas_cluster);
    metering_cluster_report(&metering_cluster_inst);
}

uint8_t get_energy_monitoring_enabled(void) {
    return energy_monitoring_enabled;
}

void energy_monitoring_tick(void) {
    if (!energy_monitoring_enabled)
        return;

    energy_meter_tick(energy_meter);

    /* Keep measurement/protection and energy persistence alive even while the
     * Zigbee network is offline. Reporting remains controlled by Zigbee's
     * configured reporting once joined. */
    electrical_measurement_cluster_update(&elec_meas_cluster);
    metering_cluster_update(&metering_cluster_inst);
}
