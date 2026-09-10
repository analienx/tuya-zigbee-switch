#include "mgmt_rtg_codec.h"

uint8_t mgmt_rtg_encode_status(uint8_t route_status,
                               bool memory_constrained,
                               bool many_to_one,
                               bool route_record_required) {
    uint8_t value = route_status & 0x07u;

    if (memory_constrained) {
        value |= 0x08u;
    }
    if (many_to_one) {
        value |= 0x10u;
    }
    if (route_record_required) {
        value |= 0x20u;
    }
    return value;
}

void mgmt_rtg_encode_descriptor(uint8_t out[MGMT_RTG_DESCRIPTOR_SIZE],
                                uint16_t destination,
                                uint8_t status_flags,
                                uint16_t next_hop) {
    out[0] = (uint8_t)destination;
    out[1] = (uint8_t)(destination >> 8);
    out[2] = status_flags;
    out[3] = (uint8_t)next_hop;
    out[4] = (uint8_t)(next_hop >> 8);
}

uint8_t mgmt_rtg_page_count(uint8_t total_entries, uint8_t start_index) {
    if (start_index >= total_entries) {
        return 0;
    }

    uint8_t remaining = (uint8_t)(total_entries - start_index);
    return remaining > MGMT_RTG_PAGE_MAX_ENTRIES
               ? MGMT_RTG_PAGE_MAX_ENTRIES
               : remaining;
}
