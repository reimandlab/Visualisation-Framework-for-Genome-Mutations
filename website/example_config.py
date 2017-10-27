"""
This example config file provides basic configuration settings, enabling fast
deployment. Note that some values has to be changed due to security reason.

Those are:
    SECRET_KEY, SQLALCHEMY_BINDS (user, pass).

You should also consider using specific host address instead of default 0.0.0.0
Note that usually to host on port 80 or so you will need to have sudo access.

You should change the name of this file to `config.py`.
"""
# -Flask generic settings
SECRET_KEY = 'replace_this'
DEBUG = True
PREFERRED_URL_SCHEME = 'http'
# use public IP addresses
DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 5000
JSONIFY_PRETTYPRINT_REGULAR = False
JSON_SORT_KEYS = False
# Maximum allowed upload file is 25MB
MAX_CONTENT_LENGTH = 25 * 1024 * 1024

# -Relational database settings
SQLALCHEMY_BINDS = {
    'cms': 'mysql://user:pass@localhost/db_cms',
    'bio': 'mysql://user:pass@localhost/db_bio'
}
SQLALCHEMY_TRACK_MODIFICATIONS = True

# -Hash-key databases settings
BDB_DNA_TO_PROTEIN_PATH = 'databases/berkley_hash.db'
BDB_GENE_TO_ISOFORM_PATH = 'databases/berkley_hash_refseq.db'

# -Application settings
# counting everything in the database in order to prepare statistics might be
# quite slow. It is helpful to turn stats generation off to speed up debugging.
LOAD_STATS = True
CONTACT_LIST = ['some_maintainer@domain.org', 'other_maintainer@domain.org']

# Should the system load local copies of third party dependencies or use content delivery networks?
#
USE_CONTENT_DELIVERY_NETWORK = True
FORBID_CONTENT_DELIVERY_NETWORK = False

UPLOAD_FOLDER = 'static/uploaded/'
UPLOAD_ALLOWED_EXTENSIONS = ['png', 'pdf', 'jpg', 'jpeg', 'gif']

# Requires PostgresSQL or Levenshtein-MySQL-UDF
SQL_LEVENSTHEIN = False
# Note: Levenshtein-MySQL-UDF must be installed in plugin_dir to use it
USE_LEVENSTHEIN_MYSQL_UDF = False

# - ReCaptcha - to activate set it to true and provide required keys
RECAPTCHA_ENABLED = False
RECAPTCHA_SITE_KEY = 'put_your_public_key_here'
RECAPTCHA_SECRET_KEY = 'put_your_private_key_here'

# - Request limiting: as we do not want to be flooded with requests here are default the requests limits (per IP)
RATELIMIT_ENABLED = True
RATELIMIT_DEFAULT = '500/hour,20/minute'
RATELIMIT_STRATEGY = 'fixed-window-elastic-expiry'

# - Scheduler configuration
SCHEDULER_ENABLED = True
SCHEDULER_API_ENABLED = False
JOBS = [
    {
        'id': 'hard_delete_expired_datasets',
        'func': 'jobs:hard_delete_expired_datasets',
        'trigger': 'interval',
        'hours': 6
    }
]


# - Celery (turned off by default as it requires Celery manual configuration)
USE_CELERY = False
CELERY_BROKER_URL = 'amqp://guest@localhost//'
CELERY_RESULT_BACKEND = 'redis://'
CELERY_TASK_SERIALIZER = 'pickle'
CELERY_RESULT_SERIALIZER = 'pickle'
CELERY_ACCEPT_CONTENT = ['pickle', 'json', 'application/json']
CELERY_IGNORE_RESULT = False
CELERY_SECURITY_KEY = 'celery/worker.key'
CELERY_SECURITY_CERTIFICATE = 'celery/worker.key.pub'
CELERY_SECURITY_CERT_STORE = 'celery/*.pub'
