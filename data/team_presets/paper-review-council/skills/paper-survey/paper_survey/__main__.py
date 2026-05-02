"""Allow running as: python -m paper_survey"""

import sys

from paper_survey import pdf_folder_batch
from paper_survey.cli import main

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "pdf-folder":
        sys.argv.pop(1)
        pdf_folder_batch.main()
    else:
        main()
