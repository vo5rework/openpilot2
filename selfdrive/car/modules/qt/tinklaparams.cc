#include "tinklaparams.h"

namespace {
Params &tinkla_params() {
  static Params params;
  return params;
}
}  // namespace

bool tinkla_get_bool_param(const std::string &tinkla_param) {
  const std::string value = tinkla_params().get(tinkla_param);
  if (value.empty()) {
    tinkla_params().putBool(tinkla_param, false);
    return false;
  }
  return value == "1";
}

void tinkla_set_bool_param(const std::string &tinkla_param,int tinkla_param_value) {
  tinkla_params().putBool(tinkla_param, tinkla_param_value != 0);
}

float tinkla_get_float_param(const std::string &tinkla_param, float default_value) {
  const std::string value = tinkla_params().get(tinkla_param);
  if (value.empty()) {
    tinkla_params().put(tinkla_param, std::to_string(default_value));
    return default_value;
  }

  try {
    return std::stof(value);
  } catch (const std::exception &) {
    tinkla_params().put(tinkla_param, std::to_string(default_value));
    return default_value;
  }
}

void tinkla_set_float_param(const std::string &tinkla_param,float tinkla_param_value) {
  tinkla_params().put(tinkla_param, std::to_string(tinkla_param_value));
}

std::string tinkla_get_str_param(const std::string &tinkla_param, std::string default_value) {
  const std::string value = tinkla_params().get(tinkla_param);
  if (value.empty()) {
    tinkla_params().put(tinkla_param, default_value);
    return default_value;
  }
  return value;
}

void tinkla_set_str_param(const std::string &tinkla_param,std::string tinkla_param_value) {
  tinkla_params().put(tinkla_param, tinkla_param_value);
}
