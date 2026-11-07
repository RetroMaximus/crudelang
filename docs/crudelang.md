<a id="top"></a>

## Table of Contents

### Functions:

[`__getattr__`](#func-__getattr__)



<a id="func-__getattr__"></a>
## Function: `__getattr__`

### Help:
> ``crudelang.global_symbol_table`` resolved lazily.

It used to be a module-level object; resolving on access keeps it pointing
at the live table even after ``reset_default_symbol_table()``.

### Usage:
```python
(name)
```

[Back to top](#top)

