#include "tongou_threshold_codec.h"
#include <string.h>

/* These are Zigbee2MQTT's exposed stock ranges, not certified trip limits. */
typedef struct { uint8_t command, selector; uint16_t min, max; uint32_t scale; } tq_spec_t;
static const tq_spec_t specs[TQ_THRESHOLD_COUNT] = {
    {TQ_CMD_E6, 0x05, 40, 100, 100},   /* temperature: centidegrees */
    {TQ_CMD_E6, 0x07, 1, 26, 1000},    /* power: W */
    {TQ_CMD_E7, 0x01, 1, 65, 1000},    /* current: mA */
    {TQ_CMD_E7, 0x03, 90, 265, 100},  /* voltage: cV */
    {TQ_CMD_E7, 0x04, 75, 240, 100},  /* voltage: cV */
};

static int channel_for(uint8_t command, uint8_t selector) {
    for (unsigned i = 0; i < TQ_THRESHOLD_COUNT; ++i)
        if (specs[i].command == command && specs[i].selector == selector)
            return (int)i;
    return -1;
}

static int valid(unsigned channel, uint8_t enabled, uint16_t value) {
    return channel < TQ_THRESHOLD_COUNT && enabled <= 1u &&
           value >= specs[channel].min && value <= specs[channel].max;
}

int tq_threshold_decode(tq_thresholds_t *state, uint8_t command,
                        const uint8_t *records, size_t length) {
    if (!state || !records || (command != TQ_CMD_E6 && command != TQ_CMD_E7) ||
        length == 0 || length % 4u || length > (command == TQ_CMD_E6 ? 8u : 12u))
        return -1;
    tq_thresholds_t proposed = *state;
    unsigned seen = 0;
    for (size_t offset = 0; offset < length; offset += 4u) {
        int channel = channel_for(command, records[offset]);
        uint16_t value = ((uint16_t)records[offset + 2u] << 8) |
                         (uint16_t)records[offset + 3u];
        if (channel < 0 || (seen & (1u << channel)) ||
            !valid((unsigned)channel, records[offset + 1u], value))
            return -1;
        seen |= 1u << channel;
        proposed.channel[channel].threshold = value;
        proposed.channel[channel].enabled = records[offset + 1u];
        proposed.channel[channel].known = 1u;
    }
    *state = proposed; /* All-or-nothing; no partial state on invalid input. */
    return 0;
}

int tq_threshold_encode_update(const tq_thresholds_t *state,
                               tq_channel_t channel, uint8_t enabled,
                               uint16_t value, uint8_t output[4]) {
    if (!state || !output || (unsigned)channel >= TQ_THRESHOLD_COUNT ||
        !state->channel[channel].known || !valid((unsigned)channel, enabled, value))
        return -1;
    output[0] = specs[channel].selector;
    output[1] = enabled;
    output[2] = (uint8_t)(value >> 8);
    output[3] = (uint8_t)value;
    return (int)specs[channel].command;
}

int tq_threshold_internal_value(tq_channel_t channel, uint16_t wire_value,
                                uint32_t *scaled) {
    if (!scaled || (unsigned)channel >= TQ_THRESHOLD_COUNT ||
        wire_value < specs[channel].min || wire_value > specs[channel].max)
        return -1;
    *scaled = (uint32_t)wire_value * specs[channel].scale;
    return 0;
}

int tq_threshold_encode_bundle(const tq_thresholds_t *state, uint8_t command,
                               uint8_t *output, size_t capacity) {
    if (!state || !output || (command != TQ_CMD_E6 && command != TQ_CMD_E7))
        return -1;
    size_t count = command == TQ_CMD_E6 ? 2u : 3u;
    size_t start = command == TQ_CMD_E6 ? 0u : 2u;
    if (capacity < count * 4u)
        return -1;
    for (size_t i = start; i < start + count; ++i)
        if (!state->channel[i].known ||
            !valid((unsigned)i, state->channel[i].enabled,
                   state->channel[i].threshold))
            return -1; /* Do not issue an incomplete protection-state report. */
    for (size_t i = 0; i < count; ++i) {
        size_t j = start + i;
        output[4u * i] = specs[j].selector;
        output[4u * i + 1u] = state->channel[j].enabled;
        output[4u * i + 2u] = (uint8_t)(state->channel[j].threshold >> 8);
        output[4u * i + 3u] = (uint8_t)state->channel[j].threshold;
    }
    return (int)(count * 4u);
}
