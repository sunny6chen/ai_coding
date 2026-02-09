"""
Skill Manager

管理 Agent 的技能，包括：
- 从 workspace 加载 skills
- 从官方目录自动加载 skills  
- 保存 skills 到 workspace
- Skill 模板管理
- Skill 验证
- 支持 Claude 官方 SKILL.md 格式（YAML frontmatter + Markdown）
"""
import json
import yaml
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from smart.schema.agent import Skill
from themis.utils.logger import logger


class SkillManager:
    """技能管理器 - 支持 Claude 官方格式"""
    
    # 官方 skills 目录（项目级别）
    OFFICIAL_SKILLS_DIR = Path(__file__).parent.parent.parent / ".qoder" / "skills"
    
    def __init__(self, workspace_path: Optional[Path] = None):
        """
        初始化技能管理器
        
        Args:
            workspace_path: 工作空间路径（包含 skills 目录）
        """
        self.workspace_path = workspace_path
        self._skills_cache: Dict[str, Skill] = {}
        
        # 设置工作空间 skills 目录
        if workspace_path:
            self.skills_dir = workspace_path / "skills"
            self.skills_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.skills_dir = None
        
        # 确保官方 skills 目录存在
        self.OFFICIAL_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"SkillManager 初始化")
        logger.info(f"  - Workspace skills: {self.skills_dir}")
        logger.info(f"  - Official skills: {self.OFFICIAL_SKILLS_DIR}")
    
    def _parse_yaml_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        """
        解析 YAML frontmatter
        
        Args:
            content: 文件内容
            
        Returns:
            (frontmatter_dict, markdown_body)
        """
        # 匹配 YAML frontmatter: ---\n...\n---
        # 闭合 --- 后的换行符设为可选，兼容末尾无换行的情况
        pattern = r'^---\s*\n(.*?)\n---\s*(?:\n(.*))?$'
        match = re.match(pattern, content, re.DOTALL)
        
        if match:
            yaml_content = match.group(1)
            markdown_body = (match.group(2) or '').strip()
            
            try:
                frontmatter = yaml.safe_load(yaml_content)
                return frontmatter or {}, markdown_body
            except yaml.YAMLError as e:
                logger.warning(f"YAML 解析失败: {e}")
                return {}, content
        
        # 没有 frontmatter，整个内容作为 markdown
        return {}, content

    def _infer_skill_name_from_md(self, file_path: Path, fallback_name: str) -> str:
        """从 Markdown 文件推断技能名称（优先 frontmatter.name）"""
        try:
            content = file_path.read_text(encoding='utf-8')
            frontmatter, _ = self._parse_yaml_frontmatter(content)
            name = frontmatter.get('name')
            if isinstance(name, str) and name.strip():
                return name.strip()
        except Exception as e:
            logger.warning(f"读取 skill 名称失败 {file_path}: {e}")
        return fallback_name
    
    def _load_skill_from_md(self, file_path: Path, skill_name: str) -> Skill:
        """
        从 Markdown 文件加载技能（Claude 格式）
        
        支持 YAML frontmatter + Markdown body 格式
        """
        content = file_path.read_text(encoding='utf-8')
        
        # 解析 YAML frontmatter
        frontmatter, markdown_body = self._parse_yaml_frontmatter(content)
        
        # 构建 Skill 对象
        skill = Skill(
            name=frontmatter.get('name', skill_name),
            description=frontmatter.get('description', ''),
            content=content,  # 完整内容（包含 frontmatter）
            instructions=markdown_body,  # 主要指令内容
        )
        
        # 添加可选字段
        optional_fields = [
            'displayName', 'author', 'version', 'license', 'repository',
            'category', 'subcategory', 'type', 'difficulty', 'audience',
            'claudeVersion', 'platform', 'languages',
            'permissions', 'inputs', 'outputs', 'examples', 'dependencies'
        ]
        
        for field in optional_fields:
            if field in frontmatter:
                skill[field] = frontmatter[field]
        
        return skill
    
    def _load_skill_from_json(self, file_path: Path, skill_name: str = "") -> Skill:
        """从 JSON 文件加载技能"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 如果 JSON 中没有 name 字段，使用文件名作为降级
        if 'name' not in data and skill_name:
            data['name'] = skill_name
        
        return Skill(**data)

    def _load_skill_from_path(self, file_path: Path, fallback_name: str) -> Optional[Skill]:
        """根据文件路径加载技能"""
        try:
            if file_path.suffix == ".md":
                skill_name = self._infer_skill_name_from_md(file_path, fallback_name)
                return self._load_skill_from_md(file_path, skill_name)
            if file_path.suffix == ".json":
                return self._load_skill_from_json(file_path, fallback_name)
        except Exception as e:
            logger.error(f"加载 skill 失败 {file_path}: {e}")
        return None
    
    def load_skill(self, skill_name: str, source: str = "auto") -> Optional[Skill]:
        """
        从文件加载单个技能
        
        Args:
            skill_name: 技能名称
            source: 来源 (auto/workspace/official)
                - auto: 先workspace后official
                - workspace: 仅workspace
                - official: 仅official
            
        Returns:
            Skill 对象，如果不存在返回 None
        """
        # 先检查缓存
        cache_key = f"{source}:{skill_name}"
        if cache_key in self._skills_cache:
            return self._skills_cache[cache_key]
        
        skill = None
        
        # 根据来源查找
        if source in ["auto", "workspace"]:
            skill = self._load_from_directory(skill_name, self.skills_dir)
            if skill and source == "workspace":
                self._skills_cache[cache_key] = skill
                return skill
        
        if not skill and source in ["auto", "official"]:
            skill = self._load_from_directory(skill_name, self.OFFICIAL_SKILLS_DIR)
        
        if skill:
            self._skills_cache[cache_key] = skill
            return skill
        
        logger.warning(f"Skill 不存在: {skill_name} (source: {source})")
        return None
    
    def _load_from_directory(self, skill_name: str, directory: Optional[Path]) -> Optional[Skill]:
        """从指定目录加载技能"""
        if not directory or not directory.exists():
            return None
        
        # 支持 .md 和 .json 格式
        skill_file_md = directory / f"{skill_name}.md"
        skill_file_json = directory / f"{skill_name}.json"
        
        try:
            if skill_file_md.exists():
                return self._load_skill_from_md(skill_file_md, skill_name)
            elif skill_file_json.exists():
                return self._load_skill_from_json(skill_file_json, skill_name)
            else:
                return self._load_from_skill_manifest(skill_name, directory)
        except Exception as e:
            logger.error(f"加载 skill 失败 {skill_name}: {e}")
        
        return None

    def _load_from_skill_manifest(self, skill_name: str, directory: Path) -> Optional[Skill]:
        """从 SKILL.md (目录级) 加载技能"""
        for skill_file in directory.rglob("SKILL.md"):
            fallback_name = skill_file.parent.name
            inferred_name = self._infer_skill_name_from_md(skill_file, fallback_name)
            if inferred_name == skill_name:
                return self._load_skill_from_md(skill_file, inferred_name)
        return None
    
    def load_all_skills(self, source: str = "auto") -> List[Skill]:
        """
        加载所有技能
        
        Args:
            source: 来源 (auto/all/workspace/official)
            
        Returns:
            技能列表
        """
        skills = []
        skill_names = set()
        
        # 加载 workspace skills
        if source in ["auto", "all", "workspace"] and self.skills_dir:
            ws_skills = self._load_all_from_directory(self.skills_dir)
            skills.extend(ws_skills)
            skill_names.update(s['name'] for s in ws_skills)
        
        # 加载官方 skills（去重）
        if source in ["auto", "all", "official"]:
            official_skills = self._load_all_from_directory(self.OFFICIAL_SKILLS_DIR)
            for skill in official_skills:
                if skill['name'] not in skill_names:
                    skills.append(skill)
                    skill_names.add(skill['name'])
        
        logger.info(f"加载了 {len(skills)} 个 skills (source: {source})")
        return skills
    
    def _load_all_from_directory(self, directory: Path) -> List[Skill]:
        """从指定目录加载所有技能"""
        if not directory.exists():
            return []
        
        skills = []
        seen_names = set()
        skill_files = self._iter_skill_files(directory)

        for file_path, fallback_name in skill_files:
            skill = self._load_skill_from_path(file_path, fallback_name)
            if not skill:
                continue
            skill_name = skill.get('name')
            if not skill_name or skill_name in seen_names:
                continue
            skills.append(skill)
            seen_names.add(skill_name)

        return skills

    def _iter_skill_files(self, directory: Path) -> List[tuple[Path, str]]:
        """枚举技能文件（根目录文件 + 子目录 SKILL.md）"""
        if not directory.exists():
            return []

        skill_files: List[tuple[Path, str]] = []
        excluded_names = {'README', 'CHANGELOG', 'LICENSE', 'CONTRIBUTING'}

        for file_path in directory.glob("*.md"):
            skill_name = file_path.stem
            if skill_name.upper() in excluded_names or file_path.name == "SKILL.md":
                continue
            skill_files.append((file_path, skill_name))

        for file_path in directory.glob("*.json"):
            skill_name = file_path.stem
            if skill_name.upper() in excluded_names:
                continue
            skill_files.append((file_path, skill_name))

        for file_path in directory.rglob("SKILL.md"):
            fallback_name = file_path.parent.name
            skill_files.append((file_path, fallback_name))

        return skill_files
    
    def save_skill(
        self, 
        skill: Skill, 
        format: str = "md",
        target: str = "workspace"
    ) -> Optional[Path]:
        """
        保存技能到文件
        
        Args:
            skill: 技能对象
            format: 文件格式 (md/json)
            target: 目标位置 (workspace/official)
            
        Returns:
            保存的文件路径
        """
        # 确定目标目录
        if target == "official":
            target_dir = self.OFFICIAL_SKILLS_DIR
        else:
            if not self.skills_dir:
                logger.error("Workspace skills 目录未设置")
                return None
            target_dir = self.skills_dir
        
        try:
            if format == "md":
                file_path = target_dir / f"{skill['name']}.md"
                # 生成 Claude 格式（YAML frontmatter + Markdown）
                content = self._generate_skill_md(skill)
                file_path.write_text(content, encoding='utf-8')
            elif format == "json":
                file_path = target_dir / f"{skill['name']}.json"
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(dict(skill), f, ensure_ascii=False, indent=2)
            else:
                logger.error(f"不支持的格式: {format}")
                return None
            
            # 更新缓存
            cache_key = f"{target}:{skill['name']}"
            self._skills_cache[cache_key] = skill
            
            logger.info(f"Skill 已保存: {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"保存 skill 失败: {e}")
            return None
    
    def _generate_skill_md(self, skill: Skill) -> str:
        """
        生成 Claude 格式的 SKILL.md 内容
        
        格式：YAML frontmatter + Markdown body
        始终从字段重新生成，确保修改后的字段能被正确保存。
        """
        # 构建 YAML frontmatter
        frontmatter = {
            'name': skill.get('name', ''),
            'description': skill.get('description', ''),
        }
        
        # 添加可选字段
        optional_fields = [
            'displayName', 'author', 'version', 'license', 'repository',
            'category', 'subcategory', 'type', 'difficulty', 'audience',
            'claudeVersion', 'platform', 'languages',
            'permissions', 'inputs', 'outputs', 'examples', 'dependencies'
        ]
        
        for field in optional_fields:
            if field in skill:
                frontmatter[field] = skill[field]
        
        # 生成 YAML 字符串
        yaml_str = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
        
        # 获取 Markdown body（优先使用 instructions，其次从 content 中提取）
        body = skill.get('instructions', '')
        if not body and 'content' in skill:
            # 从 content 中提取 markdown body（去掉 frontmatter 部分）
            _, extracted_body = self._parse_yaml_frontmatter(skill['content'])
            body = extracted_body
        
        # 组合
        return f"---\n{yaml_str}---\n\n{body}"
    
    def delete_skill(self, skill_name: str, target: str = "workspace") -> bool:
        """
        删除技能
        
        Args:
            skill_name: 技能名称
            target: 目标位置 (workspace/official)
            
        Returns:
            是否成功删除
        """
        target_dir = self.OFFICIAL_SKILLS_DIR if target == "official" else self.skills_dir
        
        if not target_dir:
            logger.error(f"{target} skills 目录未设置")
            return False
        
        try:
            # 删除文件
            deleted = False
            for ext in ['.md', '.json']:
                file_path = target_dir / f"{skill_name}{ext}"
                if file_path.exists():
                    file_path.unlink()
                    logger.info(f"Skill 文件已删除: {file_path}")
                    deleted = True
            
            # 清除缓存
            cache_key = f"{target}:{skill_name}"
            if cache_key in self._skills_cache:
                del self._skills_cache[cache_key]
            
            return deleted
            
        except Exception as e:
            logger.error(f"删除 skill 失败: {e}")
            return False
    
    def list_skills(self, source: str = "auto") -> List[str]:
        """
        列出所有技能名称
        
        Args:
            source: 来源 (auto/all/workspace/official)
            
        Returns:
            技能名称列表
        """
        skill_names = set()
        
        # Workspace skills
        if source in ["auto", "all", "workspace"] and self.skills_dir:
            skill_names.update(self._list_from_directory(self.skills_dir))
        
        # Official skills
        if source in ["auto", "all", "official"]:
            skill_names.update(self._list_from_directory(self.OFFICIAL_SKILLS_DIR))
        
        return sorted(skill_names)
    
    def _list_from_directory(self, directory: Path) -> List[str]:
        """列出目录中的所有技能"""
        if not directory.exists():
            return []

        skill_names = set()
        for file_path, fallback_name in self._iter_skill_files(directory):
            if file_path.suffix == ".md":
                skill_names.add(self._infer_skill_name_from_md(file_path, fallback_name))
            else:
                skill_names.add(fallback_name)

        return list(skill_names)
    
    def get_skill_info(self, skill_name: str, source: str = "auto") -> Optional[Dict[str, Any]]:
        """
        获取技能信息（不加载完整内容）
        
        Args:
            skill_name: 技能名称
            source: 来源 (auto/workspace/official)
            
        Returns:
            技能信息字典
        """
        # 确定搜索目录
        directories = []
        if source in ["auto", "workspace"] and self.skills_dir:
            directories.append(("workspace", self.skills_dir))
        if source in ["auto", "official"]:
            directories.append(("official", self.OFFICIAL_SKILLS_DIR))
        
        for src, directory in directories:
            if not directory.exists():
                continue

            try:
                for file_path, fallback_name in self._iter_skill_files(directory):
                    if file_path.suffix == ".md":
                        inferred_name = self._infer_skill_name_from_md(file_path, fallback_name)
                    else:
                        inferred_name = fallback_name
                    if inferred_name != skill_name:
                        continue
                    stat = file_path.stat()
                    return {
                        "name": inferred_name,
                        "format": "md" if file_path.suffix == ".md" else "json",
                        "source": src,
                        "size": stat.st_size,
                        "path": str(file_path)
                    }
            except Exception as e:
                logger.error(f"获取 skill 信息失败: {e}")
        
        return None
    
    def create_skill_from_template(
        self,
        skill_name: str,
        description: str,
        template: str = "basic",
        **metadata
    ) -> Skill:
        """
        从模板创建技能（Claude 格式）
        
        Args:
            skill_name: 技能名称（小写加连字符）
            description: 技能描述
            template: 模板类型 (basic/advanced/tool)
            **metadata: 额外的元数据字段
            
        Returns:
            创建的 Skill 对象
        """
        # 基础元数据
        skill = Skill(
            name=skill_name,
            description=description,
            displayName=metadata.get('displayName', skill_name.replace('-', ' ').title()),
            author=metadata.get('author', 'Smart Agent'),
            version=metadata.get('version', '1.0.0'),
            license=metadata.get('license', 'MIT'),
            category=metadata.get('category', 'general'),
        )
        
        # 根据模板生成内容
        templates = {
            "basic": self._template_basic(skill_name, description),
            "advanced": self._template_advanced(skill_name, description),
            "tool": self._template_tool(skill_name, description),
        }
        
        instructions = templates.get(template, templates["basic"])
        skill['instructions'] = instructions
        
        # 生成完整内容
        skill['content'] = self._generate_skill_md(skill)
        
        return skill
    
    def _template_basic(self, name: str, desc: str) -> str:
        """基础技能模板"""
        return f"""# {name.replace('-', ' ').title()}

{desc}

## 使用场景

描述这个技能的使用场景...

## 步骤

1. 第一步...
2. 第二步...
3. 第三步...

## 示例

```
示例代码或用法...
```

## 注意事项

- 注意事项 1
- 注意事项 2
"""
    
    def _template_advanced(self, name: str, desc: str) -> str:
        """高级技能模板"""
        return f"""# {name.replace('-', ' ').title()}

{desc}

## 概述

详细的技能概述...

## 前提条件

- 前提 1
- 前提 2

## 详细步骤

### 步骤 1: 准备阶段

详细说明...

### 步骤 2: 执行阶段

详细说明...

### 步骤 3: 验证阶段

详细说明...

## 最佳实践

- 最佳实践 1
- 最佳实践 2

## 常见问题

### Q: 问题 1

A: 答案 1

### Q: 问题 2

A: 答案 2

## 相关技能

- 相关技能 1
- 相关技能 2
"""
    
    def _template_tool(self, name: str, desc: str) -> str:
        """工具类技能模板"""
        return f"""# {name.replace('-', ' ').title()}

{desc}

## 工具说明

这是一个工具类技能...

## 参数

- `param1`: 参数 1 说明
- `param2`: 参数 2 说明

## 使用方法

```python
# 使用示例
tool.execute(param1="value1", param2="value2")
```

## 返回值

返回值说明...

## 错误处理

- 错误 1: 处理方法
- 错误 2: 处理方法
"""
    
    def clear_cache(self):
        """清除技能缓存"""
        self._skills_cache.clear()
        logger.info("Skill 缓存已清除")
    
    def get_stats(self, source: str = "auto") -> Dict[str, Any]:
        """
        获取统计信息
        
        Args:
            source: 来源 (auto/workspace/official/all)
            
        Returns:
            统计信息字典
        """
        stats = {
            "total_skills": 0,
            "workspace_skills": 0,
            "official_skills": 0,
            "cached_skills": len(self._skills_cache),
            "workspace_dir": str(self.skills_dir) if self.skills_dir else None,
            "official_dir": str(self.OFFICIAL_SKILLS_DIR),
        }
        
        # Workspace 统计
        if self.skills_dir and self.skills_dir.exists():
            ws_skills = self._list_from_directory(self.skills_dir)
            stats["workspace_skills"] = len(ws_skills)
        
        # Official 统计
        if self.OFFICIAL_SKILLS_DIR.exists():
            official_skills = self._list_from_directory(self.OFFICIAL_SKILLS_DIR)
            stats["official_skills"] = len(official_skills)
        
        # 总计（去重）
        if source == "all":
            all_skill_names = set()
            if self.skills_dir:
                all_skill_names.update(self._list_from_directory(self.skills_dir))
            all_skill_names.update(self._list_from_directory(self.OFFICIAL_SKILLS_DIR))
            stats["total_skills"] = len(all_skill_names)
        else:
            stats["total_skills"] = stats["workspace_skills"] + stats["official_skills"]
        
        # 计算总大小
        total_size = 0
        for directory in [self.skills_dir, self.OFFICIAL_SKILLS_DIR]:
            if directory and directory.exists():
                for file_path in directory.glob("*"):
                    if file_path.is_file():
                        total_size += file_path.stat().st_size
        
        stats["total_size_bytes"] = total_size
        stats["total_size_kb"] = round(total_size / 1024, 2)
        
        return stats
