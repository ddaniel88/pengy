# boards/board_config_c3_lolin.py

BOARD_ID = "c3_lolin"

I2C_ID = 0
I2C_SDA = 4
I2C_SCL = 5
I2C_FREQ = 100_000

# optional, if needed
MIC_ADC = None

# UART for SDS011 (recommended)
SDS011_UART_ID = 1
SDS011_UART_TX = 21
SDS011_UART_RX = 20
SDS011_UART_BAUD = 9600
