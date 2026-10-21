import os
def pid_exists(pid): return str(pid) in os.environ.get('ALIVE_PIDS', '').split(',')
