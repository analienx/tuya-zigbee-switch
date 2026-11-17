#ifndef RESET_H
#define RESET_H

#include <stdbool.h>
#include <stdint.h>

#define DEFAULT_RESET_DELAY_MS             300
#define REBOOT_CHECKPOINT_MAX_DEFERRALS    12u

__attribute__((noreturn)) void reset_all();

void schedule_full_reset(uint16_t delay_ms);
void schedule_reboot(uint16_t delay_ms);

/* Checkpoint before a controlled reboot; true once rebooting may proceed.
 * Each false return is one 5 s deferral, bounded per caller counter. */
bool reboot_checkpoint_ready(uint8_t *deferrals);

/* Reset network state while checkpointing application metering first. */
void schedule_network_reset(uint16_t delay_ms);

/* As above, running after_reset once the network state is gone and before
 * the reboot. A later schedule_network_reset() call replaces after_reset. */
void schedule_network_reset_then(uint16_t delay_ms, void (*after_reset)(void));

#endif // RESET_H
