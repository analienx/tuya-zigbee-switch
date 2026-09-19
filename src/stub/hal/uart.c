#include "hal/uart.h"

// Host stub: accept any pins and remember what was sent so driver logic can be
// exercised without real hardware. Received data can be injected in tests by
// calling the stored callback.
hal_uart_rx_callback_t stub_uart_rx_cb = 0;
uint8_t stub_uart_last_tx[16];
uint8_t stub_uart_last_tx_len = 0;

int hal_uart_init(hal_gpio_pin_t tx_pin, hal_gpio_pin_t rx_pin,
                  uint32_t baudrate, hal_uart_rx_callback_t rx_cb) {
    (void)tx_pin;
    (void)rx_pin;
    if (baudrate == 0)
        return -1;

    stub_uart_rx_cb = rx_cb;
    return 0;
}

void hal_uart_send(const uint8_t *data, uint8_t len) {
    if (len > sizeof(stub_uart_last_tx))
        len = sizeof(stub_uart_last_tx);

    for (uint8_t i = 0; i < len; i++) {
        stub_uart_last_tx[i] = data[i];
    }
    stub_uart_last_tx_len = len;
}

void hal_uart_task(void) {
    // Host stub: rx is injected directly via the stored callback.
}

void stub_uart_inject_rx(const uint8_t *data, uint16_t len) {
    if (stub_uart_rx_cb && data && len)
        stub_uart_rx_cb(data, len);
}
