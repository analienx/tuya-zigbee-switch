"""Permit-join budgets shared by the rejoin window, the campaign and the planner.

Plain constants only: importing this never loads the MQTT client stack.
"""
MIN_WINDOW_SECONDS = 30
MAX_WINDOW_SECONDS = 180
MAX_SCOPED_TOTAL_SECONDS = 2700
# 20 min: firmware boot plus one orphaned-steering rescue (5 min hold + stagger).
MAX_NETWORK_WIDE_SECONDS = 1200
