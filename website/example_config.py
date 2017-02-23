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
# use public IP adresses
DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 5000
JSONIFY_PRETTYPRINT_REGULAR = False
JSON_SORT_KEYS = False

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
# quite slow. It is helpful to turn stats generation off to speed up debuging.
LOAD_STATS = True