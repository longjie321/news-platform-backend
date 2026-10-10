# ============================================================
# 区域：测试公共配置
# 作用：让测试能 import 到项目里的模块
#   pytest 默认只把"测试文件所在目录"加进搜索路径，项目根目录反而没进，
#   直接 import config / crud / utils 会报 ModuleNotFoundError
# ============================================================

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 项目根目录 = 本文件的上一级

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
