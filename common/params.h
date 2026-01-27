#pragma once

#include <map>
#include <string>
#include <vector>
#include <iostream>
using std::cerr;
using std::cout;
using std::endl;
#include <cstdlib>

enum ParamKeyType {
  PERSISTENT = 0x02,
  CLEAR_ON_MANAGER_START = 0x04,
  CLEAR_ON_ONROAD_TRANSITION = 0x08,
  CLEAR_ON_OFFROAD_TRANSITION = 0x10,
  DONT_LOG = 0x20,
  DEVELOPMENT_ONLY = 0x40,
  ALL = 0xFFFFFFFF
};

class Params {
public:
  explicit Params(const std::string &path = {});
  // Not copyable.
  Params(const Params&) = delete;
  Params& operator=(const Params&) = delete;

  std::vector<std::string> allKeys() const;
  bool checkKey(const std::string &key);
  ParamKeyType getKeyType(const std::string &key);
  inline std::string getParamPath(const std::string &key = {}) {
    return params_path + prefix + (key.empty() ? "" : "/" + key);
  }

  // Delete a value
  int remove(const std::string &key);
  void clearAll(ParamKeyType type);

  // helpers for reading values
  std::string get(const std::string &key, bool block = false);
  
  inline bool getBool(const std::string &key, bool block = false) {
    return get(key, block) == "1";
  }
  std::map<std::string, std::string> readAll();

  inline bool tinkla_get_bool_param(const std::string &tinkla_param) {
    const std::string value = get(tinkla_param);
    if (value.empty()) {
      putBool(tinkla_param, false);
      return false;
    }
    return value == "1";
  }

  inline void tinkla_set_bool_param(const std::string &tinkla_param,int tinkla_param_value) {
      putBool(tinkla_param, tinkla_param_value != 0);
  }

  // helpers for writing values
  int put(const char *key, const char *val, size_t value_size);
  inline int put(const std::string &key, const std::string &val) {
    return put(key.c_str(), val.data(), val.size());
  }
  inline int putBool(const std::string &key, bool val) {
    return put(key.c_str(), val ? "1" : "0", 1);
  }

private:
  std::string params_path;
  std::string prefix;
};
