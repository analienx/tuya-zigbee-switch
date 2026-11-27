/* One translation unit lets the TC32 compiler eliminate private port wrappers.
 * Host acceptance compiles these same units, replacing only the SDK boundary. */
#include "nc_cfg.c"
#include "nc_ctrl.c"
#include "nc_tx.c"
#include "nc_time.c"
#include "nc_rx.c"
#ifdef NC_HOST
#include "../host/nc_port_host.c"
#else
#include "nc_port_tlsr.c"
#endif
