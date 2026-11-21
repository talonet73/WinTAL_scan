import sys
import os
import ctypes
import subprocess

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    if is_admin():
        # Già amministratore, avvia direttamente
        script_dir = os.path.dirname(os.path.abspath(__file__))
        main_py = os.path.join(script_dir, "main.py")
        if os.path.exists(main_py):
            os.chdir(script_dir)
            subprocess.Popen([sys.executable, main_py])
        else:
            print("Errore: main.py non trovato nella stessa cartella.")
            input("Premi Invio per chiudere...")
    else:
        # Richiedi elevazione
        script = os.path.abspath(__file__)
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{script}"', None, 1
        )

if __name__ == "__main__":
    run_as_admin()
