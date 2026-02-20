#include "fdcan_declarations.h"

FDCAN_GlobalTypeDef *cans[CANS_ARRAY_SIZE] = {FDCAN1, FDCAN2, FDCAN3};

static inline void fdcan_set_tfee_enabled(FDCAN_GlobalTypeDef *FDCANx, bool enabled) {
  // TFEE is routed to INT1 in llcan_init(). Gating prevents idle IRQ storms.
  if (enabled) {
    FDCANx->IE |= FDCAN_IE_TFEE;
  } else {
    FDCANx->IE &= ~FDCAN_IE_TFEE;
    FDCANx->IR |= FDCAN_IR_TFE;
  }
}

static inline bool can_queue_empty(const can_ring *q) {
  return q->w_ptr == q->r_ptr;
}


static bool can_set_speed(uint8_t can_number) {
  bool ret = true;
  FDCAN_GlobalTypeDef *FDCANx = CANIF_FROM_CAN_NUM(can_number);
  uint8_t bus_number = BUS_NUM_FROM_CAN_NUM(can_number);

  ret &= llcan_set_speed(
    FDCANx,
    bus_config[bus_number].can_speed,
    bus_config[bus_number].can_data_speed,
    bus_config[bus_number].canfd_non_iso,
    can_loopback,
    (unsigned int)(can_silent) & (1U << can_number)
  );
  return ret;
}

void can_clear_send(FDCAN_GlobalTypeDef *FDCANx, uint8_t can_number) {
  static uint32_t last_reset = 0U;
  uint32_t time = microsecond_timer_get();

  // Resetting CAN core is a slow blocking operation, limit frequency
  if (get_ts_elapsed(time, last_reset) > 100000U) {  // 10 Hz
    can_health[can_number].can_core_reset_cnt += 1U;
    can_health[can_number].total_tx_lost_cnt += (FDCAN_TX_FIFO_EL_CNT - (FDCANx->TXFQS & FDCAN_TXFQS_TFFL)); // TX FIFO msgs will be lost after reset
    llcan_clear_send(FDCANx);
    last_reset = time;
  }
}

void update_can_health_pkt(uint8_t can_number, uint32_t ir_reg) {
  uint8_t can_irq_number[3][2] = {
    { FDCAN1_IT0_IRQn, FDCAN1_IT1_IRQn },
    { FDCAN2_IT0_IRQn, FDCAN2_IT1_IRQn },
    { FDCAN3_IT0_IRQn, FDCAN3_IT1_IRQn },
  };

  FDCAN_GlobalTypeDef *FDCANx = CANIF_FROM_CAN_NUM(can_number);
  uint32_t psr_reg = FDCANx->PSR;
  uint32_t ecr_reg = FDCANx->ECR;

  can_health[can_number].bus_off = ((psr_reg & FDCAN_PSR_BO) >> FDCAN_PSR_BO_Pos);
  can_health[can_number].bus_off_cnt += can_health[can_number].bus_off;
  can_health[can_number].error_warning = ((psr_reg & FDCAN_PSR_EW) >> FDCAN_PSR_EW_Pos);
  can_health[can_number].error_passive = ((psr_reg & FDCAN_PSR_EP) >> FDCAN_PSR_EP_Pos);

  can_health[can_number].last_error = ((psr_reg & FDCAN_PSR_LEC) >> FDCAN_PSR_LEC_Pos);
  if ((can_health[can_number].last_error != 0U) && (can_health[can_number].last_error != 7U)) {
    can_health[can_number].last_stored_error = can_health[can_number].last_error;
  }

  can_health[can_number].last_data_error = ((psr_reg & FDCAN_PSR_DLEC) >> FDCAN_PSR_DLEC_Pos);
  if ((can_health[can_number].last_data_error != 0U) && (can_health[can_number].last_data_error != 7U)) {
    can_health[can_number].last_data_stored_error = can_health[can_number].last_data_error;
  }

  can_health[can_number].receive_error_cnt = ((ecr_reg & FDCAN_ECR_REC) >> FDCAN_ECR_REC_Pos);
  can_health[can_number].transmit_error_cnt = ((ecr_reg & FDCAN_ECR_TEC) >> FDCAN_ECR_TEC_Pos);

  can_health[can_number].irq0_call_rate = interrupts[can_irq_number[can_number][0]].call_rate;
  can_health[can_number].irq1_call_rate = interrupts[can_irq_number[can_number][1]].call_rate;
  // DEBUG (H7): pack current IRQ call_counters + TX state bits into irq2_call_rate.
  // This helps catch mid-second IRQ storms (fault triggers on call_counter, not call_rate).
  //
  // Packed format (uint32):
  //   upper 16 bits: IT0 current call_counter (RX/SCE line)
  //   lower 16 bits: IT1 current call_counter (TX line), rounded down to multiple of 16
  //   lower 4 bits of that lower half: TX state bitmask
  //     bit0: TFEE enabled (IE.TFEE)
  //     bit1: TFE flag set (IR.TFE)
  //     bit2: TX queue full (TXFQS.TFQF)
  //     bit3: pending TX requests (TXBRP != 0)
  uint32_t dbg = 0U;
  if ((FDCANx->IE & FDCAN_IE_TFEE) != 0U) { dbg |= 1U; }
  if ((FDCANx->IR & FDCAN_IR_TFE) != 0U) { dbg |= 2U; }
  if ((FDCANx->TXFQS & FDCAN_TXFQS_TFQF) != 0U) { dbg |= 4U; }
  if (FDCANx->TXBRP != 0U) { dbg |= 8U; }

  uint32_t rx_ctr = interrupts[can_irq_number[can_number][0]].call_counter;
  uint32_t tx_ctr = interrupts[can_irq_number[can_number][1]].call_counter;
  if (rx_ctr > 0xFFFFU) { rx_ctr = 0xFFFFU; }
  if (tx_ctr > 0xFFFFU) { tx_ctr = 0xFFFFU; }

  uint32_t packed = ((rx_ctr & 0xFFFFU) << 16) | ((tx_ctr & 0xFFF0U) | (dbg & 0xFU));
  can_health[can_number].irq2_call_rate = packed;
if (ir_reg != 0U) {
    // Clear error interrupts
    FDCANx->IR |= (FDCAN_IR_PED | FDCAN_IR_PEA | FDCAN_IR_EP | FDCAN_IR_BO | FDCAN_IR_RF0L);
    can_health[can_number].total_error_cnt += 1U;
    // Check for RX FIFO overflow
    if ((ir_reg & (FDCAN_IR_RF0L)) != 0U) {
      can_health[can_number].total_rx_lost_cnt += 1U;
    }
    // Cases:
    // 1. while multiplexing between buses 1 and 3 we are getting ACK errors that overwhelm CAN core, by resetting it recovers faster
    // 2. H7 gets stuck in bus off recovery state indefinitely
    if ((((can_health[can_number].last_error == CAN_ACK_ERROR) || (can_health[can_number].last_data_error == CAN_ACK_ERROR)) && (can_health[can_number].transmit_error_cnt > 127U)) ||
     ((ir_reg & FDCAN_IR_BO) != 0U)) {
      can_clear_send(FDCANx, can_number);
    }
  }
}

// ***************************** CAN *****************************
// FDFDCANx_IT1 IRQ Handler (TX)
void process_can(uint8_t can_number) {
  if (can_number == 0xffU) {
    return;
  }

  ENTER_CRITICAL();

  FDCAN_GlobalTypeDef *FDCANx = CANIF_FROM_CAN_NUM(can_number);
  uint8_t bus_number = BUS_NUM_FROM_CAN_NUM(can_number);

  // Clear Tx FIFO Empty flag early.
  FDCANx->IR |= FDCAN_IR_TFE;

  // Drain as much as possible per IRQ/kick to reduce IRQ rate.
  while ((FDCANx->TXFQS & FDCAN_TXFQS_TFQF) == 0U) {
    CANPacket_t to_send;
    if (!can_pop(can_queues[bus_number], &to_send)) {
      break;
    }

    if (!can_check_checksum(&to_send)) {
      can_health[can_number].total_tx_checksum_error_cnt += 1U;
      continue;
    }

    can_health[can_number].total_tx_cnt += 1U;

    uint32_t TxFIFOSA = FDCAN_START_ADDRESS + (can_number * FDCAN_OFFSET) +
                        (FDCAN_RX_FIFO_0_EL_CNT * FDCAN_RX_FIFO_0_EL_SIZE);

    // Index of the next TX FIFO element (0..FDCAN_TX_FIFO_EL_CNT-1)
    uint32_t tx_index = (FDCANx->TXFQS >> FDCAN_TXFQS_TFQPI_Pos) & 0x1FU;

    canfd_fifo *fifo = (canfd_fifo *)(TxFIFOSA + (tx_index * FDCAN_TX_FIFO_EL_SIZE));

    fifo->header[0] = (to_send.extended << 30) |
                      ((to_send.extended != 0U) ? (to_send.addr) : (to_send.addr << 18));

    // If canfd_auto is set, outgoing packets will be automatically sent as CAN-FD if an incoming CAN-FD packet was seen
    bool fd = bus_config[can_number].canfd_auto ? bus_config[can_number].canfd_enabled : (bool)(to_send.fd > 0U);
    uint32_t canfd_enabled_header = fd ? (1UL << 21) : 0UL;

    uint32_t brs_enabled_header = bus_config[can_number].brs_enabled ? (1UL << 20) : 0UL;
    fifo->header[1] = (to_send.data_len_code << 16) | canfd_enabled_header | brs_enabled_header;

    uint8_t data_len_w = (dlc_to_len[to_send.data_len_code] / 4U);
    data_len_w += ((dlc_to_len[to_send.data_len_code] % 4U) > 0U) ? 1U : 0U;
    for (uint8_t i = 0U; i < data_len_w; i++) {
      BYTE_ARRAY_TO_WORD(fifo->data_word[i], &to_send.data[i * 4U]);
    }

    FDCANx->TXBAR = (1UL << tx_index);

    // Send back to USB
    CANPacket_t to_push;

    to_push.fd = fd;
    to_push.returned = 1U;
    to_push.rejected = 0U;
    to_push.extended = to_send.extended;
    to_push.addr = to_send.addr;
    to_push.bus = bus_number;
    to_push.data_len_code = to_send.data_len_code;
    (void)memcpy(to_push.data, to_send.data, dlc_to_len[to_push.data_len_code]);
    can_set_checksum(&to_push);

    rx_buffer_overflow += can_push(&can_rx_q, &to_push) ? 0U : 1U;
  }

  refresh_can_tx_slots_available();

  // Enable TFEE only when we have pending frames and might need an IRQ to resume draining later.
  fdcan_set_tfee_enabled(FDCANx, !can_queue_empty(can_queues[bus_number]));

  EXIT_CRITICAL();
}

// FDFDCANx_IT0 IRQ Handler (RX and errors)
// blink blue when we are receiving CAN messages
void can_rx(uint8_t can_number) {
  FDCAN_GlobalTypeDef *FDCANx = CANIF_FROM_CAN_NUM(can_number);
  uint8_t bus_number = BUS_NUM_FROM_CAN_NUM(can_number);

  uint32_t ir_reg = FDCANx->IR;

  // Clear all new messages from Rx FIFO 0
  FDCANx->IR |= FDCAN_IR_RF0N;
  while((FDCANx->RXF0S & FDCAN_RXF0S_F0FL) != 0U) {
    can_health[can_number].total_rx_cnt += 1U;

    // can is live
    pending_can_live = 1;

    // get the index of the next RX FIFO element (0 to FDCAN_RX_FIFO_0_EL_CNT - 1)
    uint32_t rx_fifo_idx = (uint8_t)((FDCANx->RXF0S >> FDCAN_RXF0S_F0GI_Pos) & 0x3FU);

    // Recommended to offset get index by at least +1 if RX FIFO is in overwrite mode and full (datasheet)
    if((FDCANx->RXF0S & FDCAN_RXF0S_F0F) == FDCAN_RXF0S_F0F) {
      rx_fifo_idx = ((rx_fifo_idx + 1U) >= FDCAN_RX_FIFO_0_EL_CNT) ? 0U : (rx_fifo_idx + 1U);
      can_health[can_number].total_rx_lost_cnt += 1U; // At least one message was lost
    }

    uint32_t RxFIFO0SA = FDCAN_START_ADDRESS + (can_number * FDCAN_OFFSET);
    CANPacket_t to_push;
    canfd_fifo *fifo;

    // getting address
    fifo = (canfd_fifo *)(RxFIFO0SA + (rx_fifo_idx * FDCAN_RX_FIFO_0_EL_SIZE));

    bool canfd_frame = ((fifo->header[1] >> 21) & 0x1U);
    bool brs_frame = ((fifo->header[1] >> 20) & 0x1U);

    to_push.fd = canfd_frame;
    to_push.returned = 0U;
    to_push.rejected = 0U;
    to_push.extended = (fifo->header[0] >> 30) & 0x1U;
    to_push.addr = ((to_push.extended != 0U) ? (fifo->header[0] & 0x1FFFFFFFU) : ((fifo->header[0] >> 18) & 0x7FFU));
    to_push.bus = bus_number;
    to_push.data_len_code = ((fifo->header[1] >> 16) & 0xFU);

    uint8_t data_len_w = (dlc_to_len[to_push.data_len_code] / 4U);
    data_len_w += ((dlc_to_len[to_push.data_len_code] % 4U) > 0U) ? 1U : 0U;
    for (unsigned int i = 0; i < data_len_w; i++) {
      WORD_TO_BYTE_ARRAY(&to_push.data[i*4U], fifo->data_word[i]);
    }
    can_set_checksum(&to_push);

    // forwarding (panda only)
    CANPacket_t to_send = to_push;
    to_send.returned = 0U;
    to_send.rejected = 0U;
    int bus_fwd_num = safety_fwd_hook(bus_number, &to_send);
    if (bus_fwd_num < 0) {
      bus_fwd_num = bus_config[can_number].forwarding_bus;
      to_send = to_push;  // do not forward safety-mutated frame on fallback
      to_send.returned = 0U;
      to_send.rejected = 0U;
    }
    if (bus_fwd_num != -1) {
      to_send.bus = (uint8_t)bus_fwd_num;
      can_set_checksum(&to_send);
      can_send(&to_send, (uint8_t)bus_fwd_num, true);
      can_health[can_number].total_fwd_cnt += 1U;
    }
    safety_rx_invalid += safety_rx_hook(&to_push) ? 0U : 1U;
    ignition_can_hook(&to_push);

    led_set(LED_BLUE, true);
    rx_buffer_overflow += can_push(&can_rx_q, &to_push) ? 0U : 1U;

    // Enable CAN FD and BRS if CAN FD message was received
    if (!(bus_config[can_number].canfd_enabled) && (canfd_frame)) {
      bus_config[can_number].canfd_enabled = true;
    }
    if (!(bus_config[can_number].brs_enabled) && (brs_frame)) {
      bus_config[can_number].brs_enabled = true;
    }

    // update read index
    FDCANx->RXF0A = rx_fifo_idx;
  }

  // Error handling
  if ((ir_reg & (FDCAN_IR_PED | FDCAN_IR_PEA | FDCAN_IR_EP | FDCAN_IR_BO | FDCAN_IR_RF0L)) != 0U) {
    update_can_health_pkt(can_number, ir_reg);
  }
}

static void FDCAN1_IT0_IRQ_Handler(void) { can_rx(0); }
static void FDCAN1_IT1_IRQ_Handler(void) { process_can(0); }

static void FDCAN2_IT0_IRQ_Handler(void) { can_rx(1); }
static void FDCAN2_IT1_IRQ_Handler(void) { process_can(1); }

static void FDCAN3_IT0_IRQ_Handler(void) { can_rx(2);  }
static void FDCAN3_IT1_IRQ_Handler(void) { process_can(2); }

bool can_init(uint8_t can_number) {
  bool ret = true;

  switch (can_number) {
    case 0U:
      REGISTER_INTERRUPT(FDCAN1_IT0_IRQn, FDCAN1_IT0_IRQ_Handler, CAN_INTERRUPT_RATE, FAULT_INTERRUPT_RATE_CAN_1)
      REGISTER_INTERRUPT(FDCAN1_IT1_IRQn, FDCAN1_IT1_IRQ_Handler, CAN_INTERRUPT_RATE, FAULT_INTERRUPT_RATE_CAN_1)
      break;
    case 1U:
      REGISTER_INTERRUPT(FDCAN2_IT0_IRQn, FDCAN2_IT0_IRQ_Handler, CAN_INTERRUPT_RATE, FAULT_INTERRUPT_RATE_CAN_2)
      REGISTER_INTERRUPT(FDCAN2_IT1_IRQn, FDCAN2_IT1_IRQ_Handler, CAN_INTERRUPT_RATE, FAULT_INTERRUPT_RATE_CAN_2)
      break;
    case 2U:
      REGISTER_INTERRUPT(FDCAN3_IT0_IRQn, FDCAN3_IT0_IRQ_Handler, CAN_INTERRUPT_RATE, FAULT_INTERRUPT_RATE_CAN_3)
      REGISTER_INTERRUPT(FDCAN3_IT1_IRQn, FDCAN3_IT1_IRQ_Handler, CAN_INTERRUPT_RATE, FAULT_INTERRUPT_RATE_CAN_3)
      break;
    default:
      break;
  }

  if (can_number != 0xffU) {
    FDCAN_GlobalTypeDef *FDCANx = CANIF_FROM_CAN_NUM(can_number);
    ret &= can_set_speed(can_number);
    ret &= llcan_init(FDCANx);
    // in case there are queued up messages
    process_can(can_number);
  }
  return ret;
}

void can_deinit(uint8_t can_number) {
  if (can_number == 0xffU) {
    return;
  }

  FDCAN_GlobalTypeDef *FDCANx = CANIF_FROM_CAN_NUM(can_number);

  // Disable all FDCAN interrupts at the peripheral. (NVIC lines are disabled in board/main.c)
  FDCANx->ILE = 0U;
  FDCANx->IE = 0U;

  // Clear any pending interrupt flags.
  FDCANx->IR = 0xFFFFFFFFU;

  // Request init mode so it stops bus activity.
  FDCANx->CCCR |= FDCAN_CCCR_INIT;
}
