#pragma once

#define CANPACKET_HEAD_SIZE 6U

// If building on STM32 targets, match panda's CANFD behavior to avoid ABI mismatches
#if !defined(STM32F4) && (defined(STM32H7) || defined(STM32F7) || defined(STM32F2) || defined(STM32) || defined(PANDA))
  #ifndef CANFD
    #define CANFD
  #endif
#endif

#ifdef CANFD
  #define CANPACKET_DATA_SIZE_MAX 64U
#else
  #define CANPACKET_DATA_SIZE_MAX 8U
#endif

#ifndef CANPACKET_T_DEFINED
#define CANPACKET_T_DEFINED

typedef struct {
  unsigned char fd : 1;
  unsigned char bus : 3;
  unsigned char data_len_code : 4;  // lookup length with dlc_to_len
  unsigned char rejected : 1;
  unsigned char returned : 1;
  unsigned char extended : 1;
  unsigned int addr : 29;
  unsigned char checksum;
  unsigned char data[CANPACKET_DATA_SIZE_MAX];
} __attribute__((packed, aligned(4))) CANPacket_t;

#endif  // CANPACKET_T_DEFINED

#define GET_LEN(msg) (dlc_to_len[(msg)->data_len_code])
