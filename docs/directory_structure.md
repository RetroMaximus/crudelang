<a id="top"></a>

## Table of Contents

### Classes:

[`Platform`](#class-platform) | [`DirStruct`](#class-dirstruct)



<a id="class-platform"></a>


## Class: `Platform`


<a id="class-dirstruct"></a>


## Class: `DirStruct`


### Quick Links:


[`__init__`](#method-dirstruct-__init__) | [`get_current_mode`](#method-dirstruct-get_current_mode) | [`detect_platform`](#method-dirstruct-detect_platform) | [`detect_terminal`](#method-dirstruct-detect_terminal) | [`find_venv`](#method-dirstruct-find_venv) | [`bash_dir`](#method-dirstruct-bash_dir) | [`shell_executable`](#method-dirstruct-shell_executable) | [`run_sudo_command`](#method-dirstruct-run_sudo_command) | [`config_dir`](#method-dirstruct-config_dir) | [`data_dir`](#method-dirstruct-data_dir) | [`docs_dir`](#method-dirstruct-docs_dir) | [`log_dir`](#method-dirstruct-log_dir) | [`cache_dir`](#method-dirstruct-cache_dir) | [`system_config_dir`](#method-dirstruct-system_config_dir) | [`ensure_dirs_exist`](#method-dirstruct-ensure_dirs_exist)




<a id="method-dirstruct-__init__"></a>
### Method: `__init__`

#### Help:
> No help provided.

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj(
    app_name,
    current_mode
)
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-get_current_mode"></a>
### Method: `get_current_mode`

#### Help:
> No help provided.

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.get_current_mode()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-detect_platform"></a>
### Method: `detect_platform`

#### Help:
> No help provided.

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.detect_platform()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-detect_terminal"></a>
### Method: `detect_terminal`

#### Help:
> No help provided.

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.detect_terminal()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-find_venv"></a>
### Method: `find_venv`

#### Help:
> No help provided.

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.find_venv()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-bash_dir"></a>
### Method: `bash_dir`

#### Help:
> Returns the appropriate bin  directory that contains bash binary or cmd executable

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.bash_dir()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-shell_executable"></a>
### Method: `shell_executable`

#### Help:
> Returns the path to the appropriate shell executable (bash/cmd/powershell).

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.shell_executable()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-run_sudo_command"></a>
### Method: `run_sudo_command`

#### Help:
> Helper method to run sudo commands reliably.

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.run_sudo_command(
    s_process,
    commands
)
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-config_dir"></a>
### Method: `config_dir`

#### Help:
> Returns the appropriate config directory based on mode

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.config_dir()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-data_dir"></a>
### Method: `data_dir`

#### Help:
> Returns the appropriate data directory based on mode

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.data_dir()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-docs_dir"></a>
### Method: `docs_dir`

#### Help:
> Returns the appropriate data directory based on mode

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.docs_dir()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-log_dir"></a>
### Method: `log_dir`

#### Help:
> Returns the appropriate log directory based on mode

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.log_dir()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-cache_dir"></a>
### Method: `cache_dir`

#### Help:
> Returns the appropriate cache directory based on mode

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.cache_dir()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-system_config_dir"></a>
### Method: `system_config_dir`

#### Help:
> Returns the appropriate system config directory based on mode

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.system_config_dir()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)



<a id="method-dirstruct-ensure_dirs_exist"></a>
### Method: `ensure_dirs_exist`

#### Help:
> Creates all required directories

#### Usage:
```python
self.dirstruct_obj = DirStruct

self.dirstruct_obj.ensure_dirs_exist()
```


[Back to DirStruct](#class-dirstruct) or [Classes](#top)

