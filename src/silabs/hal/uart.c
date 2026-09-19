#include "hal/uart.h"

#include <stddef.h>

#include "em_cmu.h"
#include "em_core.h"
#include "em_gpio.h"
#include "em_usart.h"

#include "silabs/hal/silabs_gpio_utils.h"

// UART for peripheral ICs (BL0942 energy meter). Uses USART0: USART1 belongs
// to the debug VCOM iostream (excluded in release builds) and USART0 is only
// referenced by the SPI-flash bootloader variant, never by the application.
// EFR32 series 2 routes any USART to any GPIO, so no pin restrictions apply
// (unlike the TLSR8258 path).

#define BL_USART          USART0
#define BL_USART_CLOCK    cmuClock_USART0
#define BL_USART_IDX      0
#define BL_RX_IRQn        USART0_RX_IRQn

static hal_uart_rx_callback_t uart_rx_cb = NULL;
static uint8_t uart_hw_ready = 0;

// Drain every byte waiting in the RX FIFO and hand them to the callback as
// one burst. Runs in interrupt context; the BL0942 driver's rx_feed only
// touches its ring buffer, which is explicitly IRQ-safe.
static void uart_drain_rx_fifo(void) {
    uint8_t burst[16];
    uint8_t n = 0;

    while (BL_USART->STATUS & USART_STATUS_RXDATAV) {
        burst[n++] = (uint8_t)BL_USART->RXDATA;
        if (n == sizeof(burst)) {
            if (uart_rx_cb)
                uart_rx_cb(burst, n);
            n = 0;
        }
    }
    if (n && uart_rx_cb)
        uart_rx_cb(burst, n);
}

void USART0_RX_IRQHandler(void) {
    uart_drain_rx_fifo();
}

int hal_uart_init(hal_gpio_pin_t tx_pin, hal_gpio_pin_t rx_pin,
                  uint32_t baudrate, hal_uart_rx_callback_t rx_cb) {
    if (baudrate == 0 || tx_pin == HAL_INVALID_PIN || rx_pin == HAL_INVALID_PIN)
        return -1;

    GPIO_Port_TypeDef tx_port = silabs_hal_gpio_port(tx_pin);
    uint8_t           tx_no   = silabs_hal_gpio_pin_number(tx_pin);
    GPIO_Port_TypeDef rx_port = silabs_hal_gpio_port(rx_pin);
    uint8_t           rx_no   = silabs_hal_gpio_pin_number(rx_pin);

    uart_rx_cb = rx_cb;

    CMU_ClockEnable(cmuClock_GPIO, true);
    CMU_ClockEnable(BL_USART_CLOCK, true);

    // TX idles high; RX gets a pull-up so a floating line doesn't read noise.
    GPIO_PinModeSet(tx_port, tx_no, gpioModePushPull, 1);
    GPIO_PinModeSet(rx_port, rx_no, gpioModeInputPull, 1);

    USART_InitAsync_TypeDef init = USART_INITASYNC_DEFAULT; // 8N1
    init.baudrate = baudrate;
    init.enable   = usartDisable;                           // route pins before enabling
    USART_InitAsync(BL_USART, &init);

    GPIO->USARTROUTE[BL_USART_IDX].TXROUTE =
        ((uint32_t)tx_port << _GPIO_USART_TXROUTE_PORT_SHIFT) |
        ((uint32_t)tx_no << _GPIO_USART_TXROUTE_PIN_SHIFT);
    GPIO->USARTROUTE[BL_USART_IDX].RXROUTE =
        ((uint32_t)rx_port << _GPIO_USART_RXROUTE_PORT_SHIFT) |
        ((uint32_t)rx_no << _GPIO_USART_RXROUTE_PIN_SHIFT);
    // Only outputs need ROUTEEN; RX is sampled from its route directly.
    GPIO->USARTROUTE[BL_USART_IDX].ROUTEEN = GPIO_USART_ROUTEEN_TXPEN;

    USART_IntClear(BL_USART, USART_IF_RXDATAV);
    USART_IntEnable(BL_USART, USART_IEN_RXDATAV);
    NVIC_ClearPendingIRQ(BL_RX_IRQn);
    NVIC_EnableIRQ(BL_RX_IRQn);

    USART_Enable(BL_USART, usartEnable);
    uart_hw_ready = 1;
    return 0;
}

void hal_uart_send(const uint8_t *data, uint8_t len) {
    if (!uart_hw_ready || len == 0)
        return;

    // Blocking transmit: callers only send a couple of poll bytes per second
    // (~2 ms per byte at 4800 baud, interrupts stay enabled throughout).
    for (uint8_t i = 0; i < len; i++) {
        USART_Tx(BL_USART, data[i]);
    }
}

void hal_uart_task(void) {
    // Interrupt delivery is the normal path. Should the RX interrupt ever be
    // masked or lost, bytes latch in the FIFO with RXDATAV set — drain them
    // here at poll cadence as a fallback.
    if (!uart_hw_ready)
        return;

    CORE_DECLARE_IRQ_STATE;
    CORE_ENTER_ATOMIC();
    uart_drain_rx_fifo();
    CORE_EXIT_ATOMIC();
}
