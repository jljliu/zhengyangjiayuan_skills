# Zhengyangjiayuan Skills

Agent skills collection for AI assistants (Antigravity, Claude, ChatGPT, etc.).

## 📥 Download Skills

All skills are automatically packaged on every commit to `main`:

- **[Download All Skills (skills.zip)](https://github.com/jljliu/zhengyangjiayuan_skills/releases/latest/download/skills.zip)**
- **[企业网代理下载全部 Skills](https://proxy.199510000.xyz/github.com/jljliu/zhengyangjiayuan_skills/releases/latest/download/skills.zip)**

---

### Included Skills
- **`zhengyang-video-copywriter`**: Research-led product selection and video copywriting: confirm what the store sells, investigate and verify its qualities, then explain why those qualities matter in a natural shopping-advice voice.
- **`zhengyang-video-copy-reviewer`**: Review existing video copy for applicable Douyin rules, truthful quality and price claims, premium-product positioning, and respectful wording that remains clear when quoted separately; provide findings and revisions.

## 使用时自动检查更新

两个技能每次调用都先运行发行包内附的版本检查脚本；距离上次检查或成功更新超过 24 小时才联网。没有使用技能时不在后台运行，也不需要另装更新程序。每个技能独立记录检查时间，使用哪个技能就按需更新哪个技能。

检查通过 CF 代理下载 `manifest.json`，比较已安装的 Git commit SHA；有变化时下载对应版本 ZIP，验证 SHA-256，备份并替换当前技能。未变化时只下载小清单。检查失败也记录检查时间，下一次超过 24 小时的调用再试，期间继续使用现有版本。只有用户明确要求立即重查时才使用脚本的 `--force` 参数。

运行需要已有 Python 3，以及助手对网络和安装目录的相应权限；技能指令不能绕过宿主限制。更新后助手应重读新 `SKILL.md`。本地编辑过或新增了文件时保留修改，跳过自动替换并提示，不静默覆盖。

已有用户须在包含此功能的新版本发布后，重新下载安装一次 `skills.zip`，以后由技能调用时检查。源码目录没有发行版标记，开发时不会自动覆盖源码。

给用户的首次安装指令可以写成：

> 从上面的企业网代理链接下载 skills.zip，安装全部 skills，并启用它们自带的调用时更新检查：每次使用先检查本地记录，超过 24 小时再联网，有新版本则更新；不要创建后台定时任务。

## 发布机制

GitHub Actions 运行 `scripts/package_skills.py`，生成单技能和全部技能的兼容下载名、带完整 commit SHA 的版本 ZIP，以及 `manifest.json`。每个技能包自动附带 `scripts/check_skill_update.py` 与 `.release.json`（commit SHA 和文件校验值）；检查脚本只在根目录维护一份。

工作流先上传所有 ZIP，再单独发布清单；新清单不会指向尚未上传的包。版本 ZIP 保留，`skills.zip` 仍可用于手动安装。CF 代理需要避免长期缓存清单；检查请求带有时间戳查询参数及 `Cache-Control: no-cache`，版本 ZIP 可正常缓存。

本地验证：`python3 -m unittest discover -s tests -v`。本地打包预览可运行 `python3 scripts/package_skills.py --sha <完整的40位commit-SHA> --output /tmp/zhengyang-skills-release`，不执行发布。
