"""pngerr 核心 API（初始未实现）。"""


class PngLoader:
    def __init__(self):
        """构造加载器"""
        raise NotImplementedError

    def load(self, data):
        """加载 PNG，返回 (w,h)"""
        raise NotImplementedError
