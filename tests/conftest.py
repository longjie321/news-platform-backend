# 区域：测试公共配置
# 作用：让测试能 import 到项目里的模块

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 项目根目录 = 本文件的上一级

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
