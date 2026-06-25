"""PyInstaller 打包 CLI 时的入口脚本（避免相对导入问题）。

直接运行 ``pyapp.cli`` 会因为相对导入失败，这里用一个绝对导入的薄壳。
"""

import sys

from pyapp.cli import main

if __name__ == "__main__":
    sys.exit(main())
