import ctypes
import os

def dll_test(t_widget):
    dll_path = os.path.join(str(os.path.dirname(__file__)), "../libs", "libpd.dll")
    if not os.path.exists(dll_path):
        t_widget.feed(
            f"\033[32mDLL \033[33mnot found at\033[0m {dll_path}..\033\n"
        )
        return None

    try:
        pd = ctypes.CDLL(str(dll_path))

        # Define callback type for print hook
        PRINT_HOOK_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p)

        # Print hook – captures ALL console output from Pd
        @PRINT_HOOK_FUNC
        def pd_print_hook(msg_bytes):
            msg = msg_bytes.decode('utf-8', errors='replace')
            t_widget.feed(msg)   # messages already contain newline

        # Register the hook
        pd.libpd_set_printhook.argtypes = [PRINT_HOOK_FUNC]
        pd.libpd_set_printhook(pd_print_hook)

        # Initialise libpd
        t_widget.feed("\nlibpd DLL Initialization...\n")
        init_result = pd.libpd_init()

        t_widget.feed(f"\nInit result code: {init_result}\n")
        if init_result == 0:
            t_widget.feed("\n\033[32mDLL \033[33mInitialization\033[0m Successful.\033\n\n")
        else:
            t_widget.feed("\n\033[31mInitialization failed.\033\n")

        return init_result

    except OSError as e:
        t_widget.feed(f"Error loading DLL: {e}")
        return None