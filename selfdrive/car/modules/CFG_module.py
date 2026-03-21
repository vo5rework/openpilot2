
from openpilot.common.params import Params

params = Params()

def save_bool_param(param_name,param_value):
    try:
        params.put_bool(param_name, bool(param_value))
    except IOError:
        print("Failed to save "+param_name+" with value ",param_value)
    

def load_bool_param(param_name,param_def_value):
    try:
        value_saved = params.get(param_name, encoding='utf-8')
        if value_saved is None:
            raise IOError
        if value_saved not in ("0", "1"):
            raise ValueError
        return value_saved == "1"
    except IOError:
        print("Initializing "+param_name+" with value ",param_def_value)
        save_bool_param(param_name,param_def_value)
        return param_def_value
    except ValueError:
        print("Resetting "+param_name+" with value ",param_def_value)
        save_bool_param(param_name,param_def_value)
        return param_def_value

def save_float_param(param_name,param_value):
    try:
        real_param_value = param_value * 1.0
        params.put(param_name, f"{real_param_value}")
    except IOError:
        print("Failed to save "+param_name+" with value ",real_param_value)
    

def load_float_param(param_name,param_def_value):
    try:
        value_saved = params.get(param_name, encoding='utf-8')
        if value_saved is None:
            raise IOError
        return float(value_saved) * 1.0
    except IOError:
        print("Initializing "+param_name+" with value ",param_def_value*1.0)
        save_float_param(param_name,param_def_value * 1.0)
        return param_def_value * 1.0
    except ValueError:
        print("Resetting "+param_name+" with value ",param_def_value*1.0)
        save_float_param(param_name,param_def_value * 1.0)
        return param_def_value * 1.0

def save_str_param(param_name,param_value):
    try:
        params.put(param_name, str(param_value))
    except IOError:
        print("Failed to save "+param_name+" with value ",param_value)

def load_str_param(param_name,param_def_value):
    try:
        value_saved = params.get(param_name, encoding='utf-8')
        if value_saved is None:
            raise IOError
        return value_saved
    except IOError:
        print("Initializing "+param_name+" with value ",param_def_value)
        save_str_param(param_name,param_def_value)
        return param_def_value


        
