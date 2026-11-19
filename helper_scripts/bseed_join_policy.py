"""Permit-join budgets shared by the rejoin window, the campaign and the planner.

Plain constants only: importing this never loads the MQTT client stack.
"""
MIN_WINDOW_SECONDS = 30
MAX_WINDOW_SECONDS = 180
DEFAULT_WINDOW_SECONDS = 120
# Firmware boot plus both orphaned-steering rescues (each a 5 min hold plus
# stagger): the default whole-campaign budget for either join mode.
DEFAULT_TOTAL_SECONDS = 2100
MAX_SCOPED_TOTAL_SECONDS = 2700
MAX_NETWORK_WIDE_SECONDS = DEFAULT_TOTAL_SECONDS
# A permit-join request that got no answer within this long is treated as
# settled (Zigbee2MQTT handles bridge requests well within it).
OPEN_SETTLE_SECONDS = 20
