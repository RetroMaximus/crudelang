<a id="top"></a>

## Table of Contents

### Classes:

[`HelpFileGen`](#class-helpfilegen)



<a id="class-helpfilegen"></a>


## Class: `HelpFileGen`


### Quick Links:


[`__init__`](#method-helpfilegen-__init__) | [`get_log_file`](#method-helpfilegen-get_log_file) | [`init_log_file`](#method-helpfilegen-init_log_file) | [`trigger_next_help`](#method-helpfilegen-trigger_next_help) | [`_ensure_jsondata_dir_exists`](#method-helpfilegen-_ensure_jsondata_dir_exists) | [`_load_exclusion_list`](#method-helpfilegen-_load_exclusion_list) | [`_load_checksums`](#method-helpfilegen-_load_checksums) | [`_save_checksums`](#method-helpfilegen-_save_checksums) | [`_get_argument_details`](#method-helpfilegen-_get_argument_details) | [`_generate_verification_code`](#method-helpfilegen-_generate_verification_code) | [`_get_type_annotation`](#method-helpfilegen-_get_type_annotation) | [`_unparse_or_format`](#method-helpfilegen-_unparse_or_format) | [`_format_arg_line`](#method-helpfilegen-_format_arg_line) | [`_format_annotation`](#method-helpfilegen-_format_annotation) | [`_generate_metadata_block`](#method-helpfilegen-_generate_metadata_block) | [`_format_default`](#method-helpfilegen-_format_default) | [`_get_function_signature`](#method-helpfilegen-_get_function_signature) | [`_calculate_node_checksum`](#method-helpfilegen-_calculate_node_checksum) | [`_extract_help_from_docstring`](#method-helpfilegen-_extract_help_from_docstring) | [`_generate_help_content`](#method-helpfilegen-_generate_help_content) | [`generate_usage_code`](#method-helpfilegen-generate_usage_code) | [`_update_help_file_incrementally`](#method-helpfilegen-_update_help_file_incrementally) | [`to_logger`](#method-helpfilegen-to_logger) | [`generate_help_file`](#method-helpfilegen-generate_help_file)




<a id="method-helpfilegen-__init__"></a>
### Method: `__init__`

#### Help:
> Initializes the generator with the path to the module and the output file name.

Args:
    module_path (str): Path to the Python module to analyze.
    output_file (str): Name of the output Markdown file (default: 'help.md').
    overwrite (bool): Whether to overwrite the existing help file if it exists (default:
    True).
    exclusion_list (list): List of function names to exclude.
    overwrite (bool): Whether to overwrite the existing help file if it exists (default:False)
    args_separately (bool): Whether to separate the arguments into separate files (default:False)
    dest_dir (str): Destination directory (default: './').
    verbose (bool): Verbose mode (default:False).
    keep_log (bool): Whether to keep the log file (default:False).
    dir_struct (DirStruct): crossplatform directory structure handler (default:None).

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj(
    module_path,
    logger,
    output_file=None,
    exclusion_list=None,
    overwrite=False,
    args_separately=False,
    dest_dir=None,
    verbose=False,
    keep_log=False,
    dir_struct=None,
    project_logging=False,
    debug_to_console=False
)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-get_log_file"></a>
### Method: `get_log_file`

#### Help:
> No help provided.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj.get_log_file()
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-init_log_file"></a>
### Method: `init_log_file`

#### Help:
> No help provided.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj.init_log_file()
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-trigger_next_help"></a>
### Method: `trigger_next_help`

#### Help:
> No help provided.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj.trigger_next_help()
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_ensure_jsondata_dir_exists"></a>
### Method: `_ensure_jsondata_dir_exists`

#### Help:
> Ensures the .jsondata directory exists.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._ensure_jsondata_dir_exists()
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_load_exclusion_list"></a>
### Method: `_load_exclusion_list`

#### Help:
> Loads the exclusion list from a JSON file or creates it if it doesn't exist.

Returns:
    list: List of function names to exclude.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._load_exclusion_list()
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_load_checksums"></a>
### Method: `_load_checksums`

#### Help:
> Loads the checksums of previous AST nodes.

Returns:
    dict: Dictionary mapping node names to their checksums.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._load_checksums()
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_save_checksums"></a>
### Method: `_save_checksums`

#### Help:
> Saves the current checksums to file.

Args:
    checksums (dict): Dictionary mapping node names to their checksums.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._save_checksums(checksums)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_get_argument_details"></a>
### Method: `_get_argument_details`

#### Help:
> Extracts detailed argument information including types and defaults.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._get_argument_details(node)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_generate_verification_code"></a>
### Method: `_generate_verification_code`

#### Help:
> Generates a verification code in the format XXXXXX-free-XXXXXXX or XXXXXX-paid-XXXXXX.

Returns:
    str: MD5 hash of the generated verification code.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._generate_verification_code()
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_get_type_annotation"></a>
### Method: `_get_type_annotation`

#### Help:
> Extracts type annotation as string.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._get_type_annotation(annotation_node)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_unparse_or_format"></a>
### Method: `_unparse_or_format`

#### Help:
> Safely unparse or format an AST node.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._unparse_or_format(node)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_format_arg_line"></a>
### Method: `_format_arg_line`

#### Help:
> Formats a single argument line.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._format_arg_line(
    name,
    type_str,
    default
)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_format_annotation"></a>
### Method: `_format_annotation`

#### Help:
> Fallback annotation formatting.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._format_annotation(node)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_generate_metadata_block"></a>
### Method: `_generate_metadata_block`

#### Help:
> Generates a metadata block with verification information.

Returns:
    str: Formatted metadata block for the help file.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._generate_metadata_block()
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_format_default"></a>
### Method: `_format_default`

#### Help:
> Fallback default value formatting with proper None checking.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._format_default(node)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_get_function_signature"></a>
### Method: `_get_function_signature`

#### Help:
> Generates a complete signature string for a function node.

Args:
    node (ast.FunctionDef): The function node.

Returns:
    str: String representing the complete function signature.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._get_function_signature(node)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_calculate_node_checksum"></a>
### Method: `_calculate_node_checksum`

#### Help:
> Calculates a checksum for an AST node that includes docstrings and signatures.

Args:
    node (ast.AST): The AST node to checksum.

Returns:
    str: SHA256 checksum of the node's relevant attributes.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._calculate_node_checksum(node)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_extract_help_from_docstring"></a>
### Method: `_extract_help_from_docstring`

#### Help:
> Extracts help content from the docstring if present.

Args:
    docstring (str): The docstring of a function.

Returns:
    str: Extracted help content or None.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._extract_help_from_docstring(docstring)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_generate_help_content"></a>
### Method: `_generate_help_content`

#### Help:
> Walks the AST of the module and generates help content with navigation links.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._generate_help_content()
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-generate_usage_code"></a>
### Method: `generate_usage_code`

#### Help:
> No help provided.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj.generate_usage_code(
    class_name,
    name,
    usage_args
)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-_update_help_file_incrementally"></a>
### Method: `_update_help_file_incrementally`

#### Help:
> Updates the help file incrementally by only changing modified sections.

Args:
    new_content (str): The newly generated help content.
    out_path (str): Path to the help file.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj._update_help_file_incrementally(
    new_content,
    out_path
)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-to_logger"></a>
### Method: `to_logger`

#### Help:
> No help provided.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj.to_logger(
    message,
    brief_type,
    brief=None
)
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)



<a id="method-helpfilegen-generate_help_file"></a>
### Method: `generate_help_file`

#### Help:
> No help provided.

#### Usage:
```python
self.helpfilegen_obj = HelpFileGen

self.helpfilegen_obj.generate_help_file()
```


[Back to HelpFileGen](#class-helpfilegen) or [Classes](#top)

