__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import logging
import math
import os
import queue
import statistics
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait

import cv2

# пробую взяти піайв, на андроїд його нема, тоді піду старим шляхом...
try:
    import av
    AV_AVAILABLE = True
except Exception:
    av = None
    AV_AVAILABLE = False

import video_tools as vt

logger = logging.getLogger(__name__)
