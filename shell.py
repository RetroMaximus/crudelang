import logging

import crudelang
from help_gen import HelpFileGen
from directory_structure import DirStruct

app_name = "main"
current_mode = "env_mode"

dir_struct = DirStruct(app_name=f"{app_name}", current_mode=current_mode)

def to_logger(self, message, brief_type, brief=None):
    if not message:
        return

    log_levels = {
        "notset": logging.NOTSET,
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "warn": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
        "fatal": logging.CRITICAL,
        }

    if self.project_logging:
        if brief_type in log_levels:
            log_method = getattr(self.logger, brief_type, self.logger.log)
            if brief is not None:
                # this might be wrong previously did  not include the "level" argument but will be
                # highlighted yellow if removed.
                log_method(level=brief_type, msg=f"{message}: {brief}")
            else:
                # this might be wrong previously did  not include the "level" argument but will be
                # highlighted yellow if removed.
                log_method(level=logging.NOTSET, msg=f"{message}")
        else:
            level = log_levels.get(brief_type, logging.NOTSET)
            if brief is not None:
                self.logger.log(level, msg=f"{message}: {brief}")
            else:
                self.logger.log(level, msg=f"{message}")

    if self.debug_to_console:
        if brief is not None:
            print(f"{message}: {brief}")
        else:
            print(f"{message}")

help_gen = HelpFileGen(
        module_path='./',
        logger=to_logger,
        output_file='help.md',
        args_separately=False,
        dest_dir="./docs",
        dir_struct=dir_struct,
    )
help_gen.generate_help_file()

# Lang Grammar
    #
    # Expression - Term - Factor
    # '*' means 0 or more of what is before it
    # '?' means optional
    #
    #   statements  : NEWLINE* statement (NEWLINE+ statement)*
    #               : KEYWORD:CONTINUE
    #               : KEYWORD:BREAK
    #               : sugar-block          (S12: PATCH/WIDGET/ON, see below)
    #               : expr
    #

    #   expr        : KEYWORD: VAR IDENTIFIER EQ expr
    #               : comp-expr ((KEYWORD:AND|KEYWORD:OR) comp-expr)*
    #
    #   comp-expr   : NOT comp-expr
    #               : arith-expr ((EE|LT|GT|LTE|GTE) arith-expr)*
    #
    #   arith-expr  : term ((PLUS|MINUS) term)*
    #
    #   term        : factor ((MUL|DIV) factor)*
    #
    #   factor      : INT|FLOAT
    #               : (PLUS|MINUS) factor
    #               : power
    #
    #   power       : call (POW factor)*
    #
    #   call        : atom postfix*
    #   postfix     : LPAREN (expr (COMMA expr)*)? RPAREN   # call, chains: f(1)(2)
    #               : LSQUARE expr RSQUARE                  # S12: a[i] == AT(a, i)
    #
    #   atom        : INT|FLOAT|STRING|IDENTIFIER
    #               : LPAREN expr RPAREN
    #               : if-expr
    #               : for-expr
    #               : while-expr
    #               : func-def
    #
    # list-expr     : LSQUARE (expr (COMMA expr)*)? RSQUARE
    #
    #   if-expr     : KEYWORD: IF expr KEYWORD:THEN expr
    #                 (statement: if-expr-b| if-expr-c?)
    #               | (NEWLINE statements KEYWORD:END|if-expr-b|if-expr-c)
    #
    #   if-expr-b   : KEYWORD:ELIF expr KEYWORD:THEN
    #                 (statement if-expr-b|if-expr-c?)
    #               | (NEWLINE statements KEYWORD:END|if-expr-c)
    #
    #   if-expr-c   : KEYWORD:ELSE
    #                 (statement id-expr-b|if-expr-c?)
    #               | (NEWLINE statements KEYWORD:END)
    #
    #   for-expr    : KEYWORD:FOR IDENTIFIER EQ expr KEYWORD:TO expr
    #                 (KEYWORD:STEP expr)? KEYWORD:THEN
    #                 statement
    #               | (NEWLINE statements KEYWORD:END)
    #
    #   while-expr  : KEYWORD:WHILE expr KEYWORD:THEN
    #                 statement
    #               | (NEWLINE statements KEYWORD:END)
    #
    #   func-def    : KEYWORD: FUNC IDENTIFIER?
    #                 LPAREN (IDENTIFIER (COMMA IDENTIFIER)*)? RPAREN
    #                 (ARROW expr)
    #               | (NEWLINE statements KEYWORD:END)
    #
    #
    # Build In Functions
    #
    #   PRINT
    #   PRINT_RET
    #   INPUT
    #   INPUT_INT
    #   CLEAR
    #   IS_NUM
    #   IS_STR
    #   IS_LIST
    #   IS_FUNC
    #   TYPE_OF
    #   APPEND
    #   POP
    #   EXTEND
    #   LEN
    #   AT          (written l[i] since S12)
    #   SET_AT      (written l[i] = v since S12)
    #   MIN
    #   MAX
    #   CLAMP
    #   NUM
    #   STR
    #
    # S12 Tier B sugar (identifier words, never lexer keywords; each form
    # desugars at parse time onto the calls above and the ON_* bindings --
    # docs/scripting.md owns the grammar):
    #
    #   patch-block : 'PATCH' IDENTIFIER NEWLINE item* 'END'
    #   item        : 'CANVAS' expr expr [expr]
    #                 | ('OBJ'|'MSG') [IDENTIFIER] expr [at] [id]
    #                 | ('NUM'|'SYMB') IDENTIFIER [at]
    #                 | 'TABLE' IDENTIFIER expr [at] [id]
    #                 | 'TEXT' expr [at] [id]
    #                 | 'LINK' chain (',' chain)*
    #   at          : 'AT' expr expr          # omitted: authoring-grid slot
    #   id          : 'ID' IDENTIFIER         # must match a leading name
    #   chain       : ref ('->' ref)+         # ref: box name, or IN/OUT
    #                 (an IN/OUT box is injected once, at 0 0)
    #
    #   widget-block: 'WIDGET' IDENTIFIER '=' expr NEWLINE line* 'END'
    #   line        : 'AT' expr expr ['SIZE' expr expr]
    #                 | 'SET' expr expr | 'ADD' expr
    #                 | 'LINK_VALUE' expr | 'LINK_STATE' expr expr
    #                 | 'ON' EVENT expr
    #
    #   on-stmt     : 'ON' IDENTIFIER EVENT expr        # ON w CHANGE fn
    #   EVENT       : any of CHANGE CLICK KEY FRAME PAINT HIT ... (ON_EVENTS)



while True:
    text = input('crudelang > ')
    if text.strip() == "": continue
    result, error = crudelang.run('<stdin>', text)

    if error:
        print(error.as_string())
    # use the commented elif to return the 0 argument for null values
    # elif result:
    #     if len(result.elements) == 1:
    #         print(repr(result.elements[0]))
    #     else:
    #         print(result)
    elif result:
        # Filter out null (0) values
        elements = [e for e in result.elements if not (isinstance(e, crudelang.Number) and e.value == 0)]
        if len(elements) == 1:
            print(repr(elements[0]))
        elif len(elements) > 1:
            print(elements)

