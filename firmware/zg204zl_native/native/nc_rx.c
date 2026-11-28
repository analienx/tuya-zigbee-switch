#include "nc.h"

int nc_receive(uint16_t source, uint8_t endpoint, uint16_t profile, int unicast,
               int secured, const nc_addr_t *address, uint16_t cluster,
               const uint8_t *frame, unsigned n) {
    unsigned header;
    int owned;
    if (n < 3) return 1;
    header = frame[0] & 4 ? 5 : 3;
    if (n < header) return 1;
    owned = cluster == 10 || ((cluster == 6 || cluster == 8) &&
                             !(frame[0] & 3) && frame[header - 1] == 0x0b);
    if (!owned) return 0;
    if ((frame[0] & 0xef) != 8 || profile != 0x0104 || !unicast || !secured) return 1;
    if (cluster == 10) {
        if (source == 0 && endpoint == 1 && frame[2] == 1)
            nc_time_response(frame[1], frame + 3, n - 3);
    } else if (n == 5 && address) {
        nc_rejection(address, frame[1], cluster, frame[3], frame[4]);
    }
    return 1;
}
