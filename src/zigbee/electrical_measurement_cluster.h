#ifndef _ELECTRICAL_MEASUREMENT_CLUSTER_H_
#define _ELECTRICAL_MEASUREMENT_CLUSTER_H_

#include <stdint.h>
#include "hal/zigbee.h"
#include "base_components/energy_meter.h"
#include "base_components/overload_protection.h"

typedef struct {
    uint8_t endpoint;
    energy_meter_t *meter;
    uint32_t measurement_type;
    uint16_t rms_voltage;
    uint16_t rms_current;
    int16_t active_power;
    uint16_t apparent_power;
    int16_t reactive_power;
    int8_t power_factor;
    uint16_t ac_voltage_multiplier;
    uint16_t ac_voltage_divisor;
    uint16_t ac_current_multiplier;
    uint16_t ac_current_divisor;
    uint16_t ac_power_multiplier;
    uint16_t ac_power_divisor;
    uint32_t freq_cf;
    uint32_t freq_cf1;
    uint8_t sel_state;
    uint16_t calibrate_voltage;
    uint16_t calibrate_current;
    uint16_t calibrate_power;
    struct {
        uint8_t len;
        char str[36];
    } calibration_values;
    overload_protection_t overload;
    void *protected_relay;
    uint16_t overload_power_limit;
    uint16_t overload_current_limit;
    uint16_t overload_trip_delay;
    uint16_t overvoltage_warn;
    uint16_t undervoltage_warn;
    uint16_t overload_reconnect_delay;
    uint8_t overload_alarm;
    hal_zigbee_attribute attr_infos[27];
    uint32_t last_report_time;
    uint16_t last_reported_voltage;
    uint16_t last_reported_current;
    int16_t last_reported_power;
} electrical_measurement_cluster_t;

void elec_meas_derive_power(uint16_t voltage_cv, uint16_t current_ma,
                            int16_t active_power_w,
                            uint16_t *out_apparent_va,
                            int16_t *out_reactive_var,
                            int8_t *out_power_factor);
void electrical_measurement_cluster_init(
    electrical_measurement_cluster_t *cluster, energy_meter_t *meter);
void electrical_measurement_cluster_add_to_endpoint(
    electrical_measurement_cluster_t *cluster, hal_zigbee_endpoint *endpoint);
void electrical_measurement_cluster_set_protected_relay(
    electrical_measurement_cluster_t *cluster, void *relay_cluster);
void electrical_measurement_cluster_update(
    electrical_measurement_cluster_t *cluster);
void electrical_measurement_cluster_report(
    electrical_measurement_cluster_t *cluster);
void electrical_measurement_cluster_load_calibration(
    electrical_measurement_cluster_t *cluster);
void electrical_measurement_cluster_callback_attr_write_trampoline(
    uint8_t endpoint, uint16_t attribute_id);

#endif /* _ELECTRICAL_MEASUREMENT_CLUSTER_H_ */
