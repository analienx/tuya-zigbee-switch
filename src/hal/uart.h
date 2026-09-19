#ifndef _HAL_UART_H_
#define _HAL_UART_H_

#include <stdint.h>
#include "hal/gpio.h"

/**
 * Called (possibly from interrupt context) when a burst of bytes has been
 * received. Copy the data out; the buffer is only valid during the call.
 */
typedef void (*hal_uart_rx_callback_t)(const uint8_t *data, uint16_t len);

/**
 * Initialize the UART (8N1) for a peripheral like an energy metering IC.
 *
 * On TLSR8258 the RX pin must be one of the hardware UART RX pins
 * (A0/B0/B7/C3/C5/D6). The TX pin may be any GPIO: if it is not a hardware
 * UART TX pin (A2/B1/C2/D0/D3/D7), short transmissions are bit-banged with
 * interrupts briefly disabled (fine for a couple of poll bytes per second).
 *
 * @return 0 on success, -1 if the pins are unsupported on this platform
 */
int hal_uart_init(hal_gpio_pin_t tx_pin, hal_gpio_pin_t rx_pin,
                  uint32_t baudrate, hal_uart_rx_callback_t rx_cb);

/** Send a small buffer (a few command bytes). */
void hal_uart_send(const uint8_t *data, uint8_t len);

/**
 * Poll for completed receptions. Interrupt delivery is the normal path; this
 * is a robustness fallback for platforms where the RX-done interrupt may be
 * masked/lost — call it periodically (e.g. from the driver's poll task) and
 * any pending burst is delivered through the rx callback.
 */
void hal_uart_task(void);

#endif /* _HAL_UART_H_ */
