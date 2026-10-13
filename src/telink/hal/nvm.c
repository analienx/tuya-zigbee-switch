#include "hal/nvm.h"
#pragma pack(push, 1)
#include "tl_common.h"
#pragma pack(pop)
#include <stdint.h>
#include <string.h>

static hal_nvm_status_t telink_to_hal_status(nv_sts_t telink_status) {
    switch (telink_status) {
    case NV_SUCC:
        return HAL_NVM_SUCCESS;

    case NV_ITEM_NOT_FOUND:
        return HAL_NVM_NOT_FOUND;

    default:
        return HAL_NVM_ERROR;
    }
}

hal_nvm_status_t hal_nvm_write(uint8_t item_id, uint16_t size, uint8_t *data) {
    printf("Writing to NV item %d, size %d\r\n", item_id, size);
    if (data == NULL) {
        return HAL_NVM_ERROR;
    }

    nv_sts_t status = nv_flashWriteNew(1, NV_MODULE_APP, item_id, size, data);
    printf("Write status: %d\r\n", status);
    return telink_to_hal_status(status);
}

hal_nvm_status_t hal_nvm_read(uint8_t item_id, uint16_t size, uint8_t *data) {
    printf("Reading from NV item %d, size %d\r\n", item_id, size);
    if (data == NULL || size == 0) {
        return HAL_NVM_ERROR;
    }

    /* A fixed-size application record must match exactly. The SDK otherwise
     * accepts multiples of size and copies the entire stored payload. */
    uint16_t stored_size = 0;
    nv_sts_t status      = nv_flashSingleItemSizeGet(NV_MODULE_APP, item_id,
                                                     &stored_size);
    if (status != NV_SUCC)
        return telink_to_hal_status(status);

    if (stored_size != size)
        return HAL_NVM_ERROR;

    /* single=1 deletes the record on a checksum error. A retry would then see
     * NOT_FOUND and metering could overwrite the last total with zero. Locate
     * the record without mutation, then validate/read it by index instead. */
    itemIfno_t info;
    status = nv_flashReadNew(0, NV_MODULE_APP, item_id, size, (uint8_t *)&info);
    if (status != NV_SUCC)
        return HAL_NVM_ERROR; // metadata already established that it exists

    nv_info_idx_t index;
    flash_read(MODULE_IDX_START(NV_MODULE_APP, info.opSect) +
               info.opIndex * sizeof(index), sizeof(index), (uint8_t *)&index);
    if (index.offset < MODULE_CONTEXT_START(NV_MODULE_APP, info.opSect, 0) ||
        index.offset > MODULE_SECT_END(NV_MODULE_APP, info.opSect) ||
        index.size != sizeof(itemHdr_t) + size ||
        index.size > MODULE_SECT_END(NV_MODULE_APP, info.opSect) - index.offset)
        return HAL_NVM_ERROR;

    itemHdr_t header;
    flash_read(index.offset, sizeof(header), (uint8_t *)&header);
    if (header.size != size || header.itemId != item_id)
        return HAL_NVM_ERROR;

    status = nv_flashReadByIndex(NV_MODULE_APP, item_id, info.opSect,
                                 info.opIndex, size, data);
    return status == NV_SUCC ? HAL_NVM_SUCCESS : HAL_NVM_ERROR;
}

hal_nvm_status_t hal_nvm_delete(uint8_t item_id) {
    // First, get the size of the item to delete
    uint16_t item_size = 0;
    nv_sts_t status    =
        nv_flashSingleItemSizeGet(NV_MODULE_APP, item_id, &item_size);

    if (status != NV_SUCC) {
        return telink_to_hal_status(status);
    }

    // Now delete the item
    status = nv_flashSingleItemRemove(NV_MODULE_APP, item_id, item_size);
    return telink_to_hal_status(status);
}

hal_nvm_status_t hal_nvm_clear_all() {
    nv_sts_t status = nv_resetModule(NV_MODULE_APP);

    return telink_to_hal_status(status);
}
