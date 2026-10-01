# -*- coding: utf-8 -*-
"""把 Streamlit 框架自带的英文界面（右上角 Deploy、三点菜单、设置对话框等）汉化。

为什么要绕个弯：这些字符串是**框架内部渲染**的，不在我们的 Python 源码里，
没有官方配置项可改。只能注入 JS 到宿主页面，用 MutationObserver 持续监听 DOM，
把出现的英文短语替换为中文。

注入通道的排查记录（三条死路、一条活路，全部用真实浏览器验证过）：
1. components.v1.html（iframe srcdoc）—— 死路。iframe 能读 parent.document、
   手动 eval 翻译也能成功，唯独 srcdoc 里的 <script> 不执行。
2. st.markdown(unsafe_allow_html=True) 里的 <script> —— 死路。React 用
   innerHTML 插入，浏览器不执行其中的 script。
3. st.markdown 里的 <img onerror=...> —— 死路。当前版本 Streamlit 的
   markdown 渲染器会把 img 连同 onerror 整个剥离，页面上根本找不到这个元素。
4. **components.v1.iframe() 指向本应用自己的静态页面 —— 活路。**
   同源（同 host 同端口），真实 HTML 页面，<script> 必然执行，
   iframe 内可以直接读写 parent.document。

要求启动时加 --server.enableStaticServing=true，把项目下的 static/ 目录
映射到 /app/static/。i18n.html 由本模块在 inject() 时生成，
保证翻译表（TEXT_MAP）是唯一真源。
"""
import json
import os

import streamlit as st
import streamlit.components.v1 as components

# 右上角工具栏 / 三点菜单 / 设置对话框 / 页脚 的短语表
# 键必须是节点里**完整**的一段文字（去首尾空格后），不是子串 —— 避免误伤报告正文。
TEXT_MAP = {
    # 右上角
    "Deploy": "部署",
    "Deploy this app": "部署此应用",
    "Share": "分享",
    "Copy link": "复制链接",
    "Fork this app": "复制此应用",
    "Source code": "源代码",
    "Stop": "停止",
    "Running": "运行中",
    "Rerun": "重新运行",
    "Always rerun": "总是自动重跑",
    "Auto rerun": "自动重跑",
    "Record screen": "录制屏幕",
    "System": "跟随系统",
    # 三点菜单
    "Report a bug": "报告问题",
    "Get help": "获取帮助",
    "About": "关于",
    "Settings": "设置",
    "Clear cache": "清除缓存",
    "Print": "打印",
    "Record a screencast": "录制屏幕",
    "Developer options": "开发者选项",
    "Edit theme": "编辑主题",
    "Theme creator": "主题编辑器",
    "Keyboard shortcuts": "键盘快捷键",
    "Documentation": "文档",
    "Ask a question": "提问",
    "Community forum": "社区论坛",
    "Streamlit cheatsheet": "Streamlit 速查表",
    # 设置对话框
    "Appearance": "外观",
    "Theme": "主题",
    "Choose app theme, colors and fonts": "选择应用主题、颜色与字体",
    "Wide mode": "宽屏模式",
    "Show sidebar navigation": "显示侧边栏导航",
    "Light": "浅色",
    "Dark": "深色",
    "Use system setting": "跟随系统",
    "Active theme": "当前主题",
    "Custom Theme": "自定义主题",
    "Editing": "编辑中",
    "Viewer": "查看器",
    "Save changes": "保存更改",
    "Cancel": "取消",
    "Back": "返回",
    "OK": "确定",
    # 清除缓存确认框
    "Clear caches for this app": "清除本应用的缓存",
    "This will remove the cached entries for functions and data.": "将移除函数与数据的缓存条目。",
}

# 前缀匹配表：整段文字**以这些开头**时替换前缀。
# 用在文本里带版本号等变动部分的场景 —— 菜单底部的版本行实际是
# "Made with Streamlit v1.64.0"，精确匹配永远命不中。
PREFIX_MAP = {
    "Made with Streamlit": "由 Streamlit 构建",
    "Streamlit v": "Streamlit v",
}

_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<script>
(function () {
  var doc;
  try { doc = window.parent.document; } catch (e) { return; }
  if (!doc) { return; }

  var MAP = %MAP%;
  var PREFIX = %PREFIX%;

  function tr(node) {
    var raw = node.nodeValue;
    if (!raw) { return; }
    var t = raw.trim();
    if (!t) { return; }
    if (Object.prototype.hasOwnProperty.call(MAP, t)) {
      node.nodeValue = raw.replace(t, MAP[t]);
      return;
    }
    // 前缀匹配：处理 "Made with Streamlit v1.64.0" 这类带版本号的文本。
    // 替换后文本不再以该前缀开头，天然幂等，不会死循环。
    for (var k in PREFIX) {
      if (Object.prototype.hasOwnProperty.call(PREFIX, k) && t.indexOf(k) === 0) {
        node.nodeValue = raw.replace(k, PREFIX[k]);
        return;
      }
    }
  }

  function walk(root) {
    if (!root) { return; }
    var it = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
    var n;
    while ((n = it.nextNode())) { tr(n); }
  }

  walk(doc.body);

  new MutationObserver(function (muts) {
    for (var i = 0; i < muts.length; i++) {
      var m = muts[i];
      if (m.type === "characterData") {
        tr(m.target);
      } else {
        for (var j = 0; j < m.addedNodes.length; j++) {
          var nd = m.addedNodes[j];
          if (nd.nodeType === Node.TEXT_NODE) { tr(nd); }
          else if (nd.nodeType === Node.ELEMENT_NODE) { walk(nd); }
        }
      }
    }
  }).observe(doc.body, { childList: true, subtree: true, characterData: true });

  // 双保险：React 有时整棵重渲染把翻译冲掉，低频轮询兜底
  setInterval(function () { walk(doc.body); }, 2000);
})();
</script>
</body></html>
"""

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_PAGE = os.path.join(_STATIC_DIR, "i18n.html")


def _ensure_page() -> bool:
    """把翻译页写到 static/i18n.html。内容一致就不重写（避免触发不必要的重载）。

    返回 False 表示写不进去（如只读文件系统）—— 那汉化就安静降级，不影响主体功能。
    """
    body = (_HTML
            .replace("%MAP%", json.dumps(TEXT_MAP, ensure_ascii=False))
            .replace("%PREFIX%", json.dumps(PREFIX_MAP, ensure_ascii=False)))
    try:
        if os.path.exists(_PAGE):
            with open(_PAGE, encoding="utf-8") as f:
                if f.read() == body:
                    return True
        os.makedirs(_STATIC_DIR, exist_ok=True)
        with open(_PAGE, "w", encoding="utf-8") as f:
            f.write(body)
        return True
    except OSError:
        return False


def inject() -> None:
    """在页面上注入汉化。需要在 st.set_page_config 之后调用。

    静态服务没开时 iframe 只会 404，不影响主体功能（汉化静默失效）。
    开关固化在 .streamlit/config.toml 的 server.enableStaticServing。

    ⚠️ 别换成 st.iframe（2026-09-24 实测过，会坏）：
      Streamlit 1.64 会对 components.v1.iframe 打弃用警告
      （「will be removed after 2026-06-01」），看着像该换了；
      但 st.iframe 存在且签名兼容（width/height 都收 int），换完**汉化直接失效** ——
      工具栏又变回 Deploy、菜单又变回 Clear cache。
      推断是新 API 给 iframe 加了 sandbox，脚本不再执行，于是翻译逻辑跑不起来。
      升级 Streamlit 时若旧 API 真被移除，要重新验证的是「iframe 里 hasSandbox」
      这件事，而不是照着弃用警告改。验证脚本：tools/verify_i18n.py
    """
    if not _ensure_page():
        return
    components.iframe("/app/static/i18n.html", height=0, width=0)
