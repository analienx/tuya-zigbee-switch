"""Permit-join budgets shared by the rejoin window, the campaign and the planner.

Plain constants only: importing this never loads the MQTT client stack.
"""
MIN_WINDOW_SECONDS = 30
MAX_WINDOW_SECONDS = 180
DEFAULT_WINDOW_SECONDS = 120
MAX_SCOPED_TOTAL_SECONDS = 2700
# 35 min: firmware boot plus both orphaned-steering rescues (each a 5 min hold
# plus stagger), the same span the scoped default always covered.
MAX_NETWORK_WIDE_SECONDS = 2100
