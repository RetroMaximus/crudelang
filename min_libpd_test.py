import ctypes
import os


dll_path = os.path.join(str(os.path.dirname(__file__)), "./libs", "libpd.dll")
if not os.path.exists(dll_path):
    print(
        f"\033[32mDLL \033[33mnot found at\033[0m {dll_path}..\033\n"
    )
    exit(0)


dll = ctypes.CDLL(dll_path)
dll.libpd_init()

# Create a trivial patch in a known location
patch_dir = r".\temp"
os.makedirs(patch_dir, exist_ok=True)
patch_path = os.path.join(patch_dir, "test.pd")
with open(patch_path, "w") as f:
    f.write("#N canvas 317 173 524 477 12;\n")
    f.write("#X obj 167 64 loadbang;\n")
    f.write("#X obj 224 358 print;\n")
    f.write("#X obj 224 301 metro 1 120 permin, f 19;\n")
    f.write("#X obj 175 185 tgl 19 0 empty empty empty 17 7 0 10 #dfdfdf #000000 #000000 0 1;\n")
    f.write("#X msg 374 146 0.5;\n")
    f.write("#X msg 410 142 1;\n")
    f.write("#X msg 339 146 4;\n")
    f.write("#X msg 217 185 tempo 80 permin;\n")
    f.write("#X floatatom 217 149 5 0 0 0 - - - 0;\n")
    f.write("#X floatatom 354 275 5 0 0 0 - - - 0;\n")
    f.write("#X text 122 186 on/off;\n")
    f.write("#X obj 354 251 expr 1/$f1;\n")
    f.write("#X msg 217 119 80;\n")
    f.write("#X msg 167 152 1;\n")
    f.write("#X msg 76 126 0;\n")
    f.write("#X obj 75 16 r \\$0-stop-metro;\n")
    f.write("#X obj 130 40 r \\$0-stop-metro;\n")
    f.write("#X obj 131 125 t b;\n")
    f.write("#X obj 224 332 t b;\n")
    f.write("#X obj 354 226 f;\n")
    f.write("#X floatatom 353 176 5 0 0 1 beat_length - - 0;\n")
    f.write("#X obj 168 88 t b b b b;\n")
    f.write("#X obj 353 201 t b f;\n")
    f.write("#X connect 0 0 21 0;\n")
    f.write("#X connect 2 0 18 0;\n")
    f.write("#X connect 3 0 2 0;\n")
    f.write("#X connect 4 0 20 0;\n")
    f.write("#X connect 5 0 20 0;\n")
    f.write("#X connect 6 0 20 0;\n")
    f.write("#X connect 7 0 2 0;\n")
    f.write("#X connect 8 0 7 0;\n")
    f.write("#X connect 9 0 2 1;\n")
    f.write("#X connect 11 0 9 0;\n")
    f.write("#X connect 12 0 8 0;\n")
    f.write("#X connect 13 0 3 0;\n")
    f.write("#X connect 14 0 3 0;\n")
    f.write("#X connect 15 0 14 0;\n")
    f.write("#X connect 16 0 17 0;\n")
    f.write("#X connect 17 0 13 0;\n")
    f.write("#X connect 18 0 1 0;\n")
    f.write("#X connect 19 0 11 0;\n")
    f.write("#X connect 20 0 22 0;\n")
    f.write("#X connect 21 0 13 0;\n")
    f.write("#X connect 21 1 12 0;\n")
    f.write("#X connect 21 3 5 0;\n")
    f.write("#X connect 22 0 19 0;\n")
    f.write("#X connect 22 1 19 1;\n")
    

# Set argument types for libpd_openfile
dll.libpd_openfile.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
dll.libpd_openfile.restype = ctypes.c_void_p

# Set argument types for libpd_closefile
dll.libpd_closefile.argtypes = [ctypes.c_void_p]
dll.libpd_closefile.restype = None  # void

handle = dll.libpd_openfile(b"test.pd", patch_dir.encode())
print(f"\nHandle: {handle}")
if handle:

    print("Dollar:", dll.libpd_getdollarzero())
    
    print("\nBlock size:", dll.libpd_blocksize())

    inChannels = 2
    outChannels = 2
    sampleRate = 44100
    print("\nAudio Init --------------------------")
    print("Audio in Channels:", inChannels)
    print("Audio out Channels:", outChannels)
    print("Audio sample rate:", sampleRate)

    try:
        dll.libpd_init_audio(inChannels, outChannels, sampleRate)
        print("Audio Init Complete -----------------")
    except Exception as e:
        print(e)
        exit(0)

    print("\nDSP is now Off -------------------")
    try:
        dll.libpd_start_message(1)
        dll.libpd_add_float(1)
        dll.libpd_finish_message("pd", "dsp")
        print("DSP is now On -------------------")
    except Exception as e:
        print(e)
        exit(0)

    pd_wish_path = "/c/Program Files/Pd/bin/wish.exe"
    #dll.libpd_start_gui(pd_wish_path)

    #dll.libpd_closefile(handle)
else:
    print("Open failed")
