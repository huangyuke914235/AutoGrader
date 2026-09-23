# Gitee Pages 镜像部署（国内访问兜底）

> 为什么要做：`github.io` 在国内访问**有波动**，评委打不开主页就看不到离线案例。
> Gitee Pages 是同一个 `docs/` 目录的静态镜像，两个链接一起提交，哪个能开用哪个。

## 一、先弄清楚一件事：镜像的是"主页"，不是"可交互应用"

- Gitee Pages 只能托管**静态文件**，跑不了 Python。
- 所以镜像的是 `docs/index.html`（作品主页 + 离线可看案例）。
- **可交互 Demo 仍然只有 Streamlit Cloud 那一条链接**，两个链接一起交上去。

## 二、部署步骤（约 15 分钟 + 等待审核）

### 1. 建仓库并同步代码

1. 登录 [gitee.com](https://gitee.com) → 右上角 **+** → **新建仓库**
2. 仓库名建议同样叫 `AutoGrader`，**设为公开**，其他默认，点创建
3. 在仓库页点 **导入** → **从 GitHub / GitLab 导入**（推荐，最省事）
   - 填 `https://github.com/huangyuke914235/AutoGrader`
   - 勾选"同步/私有导入"随意，公开即可
   - 等待导入完成

> 备选：也可以在本地加第二个远程地址，然后推给 Gitee：
> ```bash
> git remote add gitee https://gitee.com/<你的用户名>/AutoGrader.git
> git push gitee main
> ```
> （用 GitHub Desktop 的话，直接走网页端"导入"更简单。）

### 2. 开启 Gitee Pages

1. 进入 Gitee 仓库 → 顶部菜单 **服务** → **Gitee Pages**
2. 部署分支选 `main`，**部署目录填 `docs`**（这一步最容易填错，填了根目录会 404）
3. 勾选/确认后点 **启动**
4. 首次使用需要**实名认证**（上传身份证或人脸识别），审核通常几分钟到几小时
5. 拿到地址：`https://<你的用户名>.gitee.io/AutoGrader`

### 3. 验证（必做）

打开上面那个地址，逐项确认：

- [ ] 页面能正常打开，样式没丢
- [ ] **离线案例能加载**（点"加载案例"，能看到报告正文与高亮证据）
  —— 这是最重要的一项，评委打不开 Demo 时全靠它
- [ ] "导出 CSV"按钮能用
- [ ] 三个数字区和四次评测对照表显示正常

> 案例数据在 `docs/cases/` 下（S02 / S03 / S04），是静态 JSON，镜像过去就能用。

## 三、切到镜像只需改一行

主页里所有仓库链接都由同一个常量派生：

```js
var REPO_URL  = "https://github.com/huangyuke914235/AutoGrader";
```

在 **Gitee 那份代码**里把它改成：

```js
var REPO_URL  = "https://gitee.com/<你的用户名>/AutoGrader";
```

改完这一行，正文里 4 个"排查记录"链接也会一起指向 Gitee（这是 2026-09-23 专门改的），
不用逐个替换。

> 最简单的做法：两边都保留 GitHub 版本即可——文档链接指向 GitHub 只是国内打开慢一点，
> 不影响评审。如果追求完美，就在 Gitee 仓库里改那一行。

## 四、两个链接一起提交

提交专区里把这两条都填上：

| 用途 | 链接 |
|---|---|
| 作品主页（主） | `https://<用户名>.github.io/AutoGrader/` |
| 作品主页（国内镜像） | `https://<用户名>.gitee.io/AutoGrader` |
| 可交互 Demo | `https://autograder-szu.streamlit.app` |
| 源码仓库 | GitHub 与 Gitee 都填 |

## 五、注意事项

- **Gitee Pages 更新需要手动点"更新"**：以后 GitHub 那边改了主页，
  要在 Gitee Pages 页面点一次 **更新** 才会重新部署，不会自动同步。
- **不要拖到 9/26 晚上才做**：实名认证可能要等审核，我建议 9/25 前完成。
- Gitee 仓库同样**不能**包含 `data/` 下的原始报告——我们的 `.gitignore` 已经排除了，
  导入时不会带过去。
- 如果 Gitee 要求仓库必须公开才给 Pages，那就公开；里面的内容本来就是可以公开的。
