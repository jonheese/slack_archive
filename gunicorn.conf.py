"""Conservative defaults for a 2-vCPU server shared with MySQL."""
bind = '0.0.0.0:5001'
workers = 2
worker_class = 'sync'
timeout = 120
accesslog = '-'
# Log paths, not query strings (which contain private search text).
access_log_format = '%(h)s %(m)s %(U)s %(s)s %(L)s'
errorlog = '-'
capture_output = True
