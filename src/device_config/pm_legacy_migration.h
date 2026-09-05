#ifndef _PM_LEGACY_MIGRATION_H_
#define _PM_LEGACY_MIGRATION_H_

#include <stdbool.h>

/* Copy accepted BSEED PM state from the historical metering fork into the
 * unified V8 NVM namespace. Returns false only for an exact BSEED PM target
 * whose legacy/new state could not be read or verified safely. Other builds
 * and other identities are no-ops. */
bool migrate_legacy_bseed_pm_nvm(void);

#endif /* _PM_LEGACY_MIGRATION_H_ */
