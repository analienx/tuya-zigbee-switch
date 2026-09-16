#ifndef TELINK_MGMT_RTG_CODEC_H
#define TELINK_MGMT_RTG_CODEC_H

#include <stdbool.h>
#include <stdint.h>

#define MGMT_RTG_RESPONSE_HEADER_SIZE    5u
#define MGMT_RTG_DESCRIPTOR_SIZE         5u
#define MGMT_RTG_PAGE_MAX_ENTRIES        9u

uint8_t mgmt_rtg_encode_status(uint8_t route_status,
                               bool memory_constrained,
                               bool many_to_one,
                               bool route_record_required);

void mgmt_rtg_encode_descriptor(uint8_t out[MGMT_RTG_DESCRIPTOR_SIZE],
                                uint16_t destination,
                                uint8_t status_flags,
                                uint16_t next_hop);

uint8_t mgmt_rtg_page_count(uint8_t total_entries, uint8_t start_index);

#endif
