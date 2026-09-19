#ifndef TONGOU_THRESHOLD_CODEC_H
#define TONGOU_THRESHOLD_CODEC_H

#include <stddef.h>
#include <stdint.h>

/* Pure stock-wire codec. No GPIO, NVM, Zigbee identity or protection actions.
 * Values are WIRE units: degrees C, kW, A and V (not internal SI-scaled values).
 * Do not expose this as a protection implementation or stock-compatible image. */
#define TQ_THRESHOLD_COUNT    5u
#define TQ_CMD_E6             0xE6u
#define TQ_CMD_E7             0xE7u

typedef enum {
    TQ_TEMPERATURE   = 0,
    TQ_POWER         = 1,
    TQ_CURRENT       = 2,
    TQ_OVER_VOLTAGE  = 3,
    TQ_UNDER_VOLTAGE = 4
} tq_channel_t;

typedef struct {
    uint16_t threshold;
    uint8_t  enabled;
    uint8_t  known;
} tq_threshold_t;

typedef struct {
    tq_threshold_t channel[TQ_THRESHOLD_COUNT];
} tq_thresholds_t;

/* Returns 0 on success. All errors leave destination unchanged. */
int tq_threshold_decode(tq_thresholds_t *state, uint8_t command,
                        const uint8_t *records, size_t length);

/* A single record is permitted only when the whole companion pair is known. */
int tq_threshold_encode_update(const tq_thresholds_t *state,
                               tq_channel_t channel, uint8_t enabled,
                               uint16_t value, uint8_t output[4]);

/* E6 produces 8 bytes and E7 produces 12; requires every field known. */
int tq_threshold_encode_bundle(const tq_thresholds_t *state, uint8_t command,
                               uint8_t *output, size_t capacity);

/* Converted thresholds for internal comparison; no actuator side effects. */
int tq_threshold_internal_value(tq_channel_t channel, uint16_t wire_value,
                                uint32_t *scaled);

#endif
