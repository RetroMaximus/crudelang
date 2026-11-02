import ast
import json
import logging
import os
import hashlib
import platform
from datetime import datetime
from pathlib import Path
from random import randint
from typing import Dict, List, Optional


class HelpFileGen:
    def __init__(
            self, module_path, logger, output_file=None, exclusion_list=None, overwrite=False,
            args_separately=False, dest_dir=None, verbose=False, keep_log=False, dir_struct=None, project_logging=False, debug_to_console=False
    ):
        """Initializes the generator with the path to the module and the output file name.

        Args:
            module_path (str): Path to the Python module to analyze.
            output_file (str): Name of the output Markdown file (default: 'help.md').
            overwrite (bool): Whether to overwrite the existing help file if it exists (default:
            True).
            exclusion_list (list): List of function names to exclude.
            overwrite (bool): Whether to overwrite the existing help file if it exists (default:False)
            args_separately (bool): Whether to separate the arguments into separate files (default:False)
            dest_dir (str): Destination directory (default: './').
            verbose (bool): Verbose mode (default:False).
            keep_log (bool): Whether to keep the log file (default:False).
            dir_struct (DirStruct): crossplatform directory structure handler (default:None).
        """
        _ = exclusion_list

        self.verbose = verbose
        self.keep_log = keep_log
        self.dir_struct = dir_struct
        if self.dir_struct is None:
            # Fallback: use a default log directory
            self.log_dir = Path("./logs")
        else:
            self.log_dir = Path(self.dir_struct.log_dir)

        # Create the log directory (if needed)
        os.makedirs(self.log_dir, exist_ok=True)

        # Now use self.log_dir for the log file path
        log_file = self.get_log_file()


        self.debug_to_console = debug_to_console

        self.project_logging = project_logging

        self.log_file_path = self.init_log_file()

        # if hasattr(self.dir_struct, "log_dir"):
        log_dir = Path(self.dir_struct.log_dir)
        os.makedirs(log_dir, exist_ok=True)

        log_file = self.get_log_file()

        log_level = logging.DEBUG if self.verbose else logging.INFO
        log_mode = 'a' if self.keep_log else 'w'

        logging.basicConfig(
            filename=log_file,
            level=log_level,
            format='%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            filemode=log_mode
        )
        self.logger = logging

        self.module_path = module_path
        self._already_reported_up_to_date = set()
        # self.output_file = f"{module_path.replace('.py', '').replace(' ', '_')}-{output_file}"
        self.output_file = f"{output_file}"
        self.overwrite = overwrite
        self.args_seperatly = args_separately
        self.license_type = "free"
        self.exclusion_list = self._load_exclusion_list() or []
        self.checksum_file = os.path.join(
            ".jsondata", f"{os.path.basename(module_path)}.checksums.json",
        )
        self.dest_dir = dest_dir
        self._ensure_jsondata_dir_exists()
        self.generate_help_file()

    def get_log_file(self):
        # Use self.log_dir if available, otherwise fallback to dir_struct
        log_dir = self.log_dir if hasattr(self, 'log_dir') else Path(self.dir_struct.log_dir)
        system = platform.system().lower()
        if system == 'windows':
            if self.dir_struct.detect_terminal() in [
                "ucrt64", "clang", "x86_64-pc-msys", "msys", "mingw64", "msys2", "msys2-64",
                "x86_64"
            ]:
                return Path(
                    f"{log_dir}/session_{datetime.now().strftime('%Y-%m-%d')}.log"
                )
            else:
                return Path(
                    f"{log_dir}\\session_{datetime.now().strftime('%Y-%m-%d')}.log"
                )
        elif system == 'linux':
            return Path(
                f"{log_dir}/session_{datetime.now().strftime('%Y-%m-%d')}.log"
            )
        elif system == 'darwin':
            return Path(
                f"{log_dir}/session_{datetime.now().strftime('%Y-%m-%d')}.log"
            )
        else:
            return Path(
                f"{log_dir}/session_{datetime.now().strftime('%Y-%m-%d')}.log"
            )

    def init_log_file(self):
        log_dir = self.log_dir if hasattr(self, 'log_dir') else Path(self.dir_struct.log_dir)

        os.makedirs(log_dir, exist_ok=True)
        log_file = self.get_log_file()
        if not self.keep_log and os.path.exists(log_file):
            with open(log_file, 'w') as f:
                f.write(f"--- New Session Started ---\n")
        else:
            with open(log_file, 'a') as f:
                f.write(f"\n--- Resuming Session ---\n")

        return log_file


    def trigger_next_help(self):
        self.generate_help_file()

    def _ensure_jsondata_dir_exists(self) -> None:
        """Ensures the .jsondata directory exists."""
        if not os.path.exists(".jsondata"):
            os.makedirs(".jsondata")

    def _load_exclusion_list(self) -> List[str]:
        """Loads the exclusion list from a JSON file or creates it if it doesn't exist.

        Returns:
            list: List of function names to exclude.
        """
        exclusion_path = ".jsondata/exclude_help_ast.json"
        self._ensure_jsondata_dir_exists()

        if not os.path.exists(exclusion_path):
            with open(exclusion_path, "w", encoding="utf-8") as f:
                json.dump(["dist", "build", ".verclock", "verclock", ".venv", "venv", "env", "__pycache__", "venv313",
                           "venv312", "venv314", ".pytest_cache", ".mypy_cache",
                           "node_modules", ".npm", ".yarn", "dist", "build", ".build", "target",
                           ".target", ".gradle", ".mvn", ".idea", ".vscode", ".vs", ".git", ".svn",
                           ".hg", ".bzr", ".DS_Store", "Thumbs.db", ".classpath", ".project",
                           ".settings", "bin", "obj", "out", ".redhat", ".gnupg", ".vsconfig",
                           ".next", ".nuxt", ".cache", ".parcel-cache", ".webpack", ".serverless",
                           ".QtWebEngineProcess", ".bun", ".conda", ".cargo", ".dotnet", ".gitconfig",
                           ".jetbrains", ".logs", ".mamba", ".ssh", "AppData", "Application Data",
                           ".rustup", ".thumbnails", ".streamcue", ".templateengine",
                           "Cookies", "Contacts", "logs", ".logs", ".jsondata", "jsondata", "docs", ".docs", "temp", "tests","help_gen.py"], f)
            return []

        with open(exclusion_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_checksums(self) -> Dict[str, str]:
        """Loads the checksums of previous AST nodes.

        Returns:
            dict: Dictionary mapping node names to their checksums.
        """
        if not os.path.exists(self.checksum_file):
            return {}

        with open(self.checksum_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_checksums(self, checksums: Dict[str, str]) -> None:
        """Saves the current checksums to file.

        Args:
            checksums (dict): Dictionary mapping node names to their checksums.
        """
        with open(self.checksum_file, "w", encoding="utf-8") as f:
            json.dump(checksums, f, indent=2)

    def _get_argument_details(self, node: ast.FunctionDef) -> List[str]:
        """Extracts detailed argument information including types and defaults."""
        arg_details = []

        # Process positional arguments
        for i, arg in enumerate(node.args.args):
            arg_name = arg.arg
            arg_type = self._get_type_annotation(arg.annotation)

            # Handle defaults for positional args
            default_value = None
            if node.args.defaults and i >= (len(node.args.args) - len(node.args.defaults)):
                default_idx = i - (len(node.args.args) - len(node.args.defaults))
                default_node = node.args.defaults[default_idx]
                default_value = self._unparse_or_format(default_node)

            arg_details.append(self._format_arg_line(arg_name, arg_type, default_value))

        # Process keyword-only arguments
        for i, arg in enumerate(node.args.kwonlyargs):
            arg_name = arg.arg
            arg_type = self._get_type_annotation(arg.annotation)

            # Handle keyword argument defaults
            default_value = None
            if i < len(node.args.kw_defaults) and node.args.kw_defaults[i] is not None:
                default_node = node.args.kw_defaults[i]
                if default_node is not None:  # Double check
                    default_value = self._unparse_or_format(default_node)

            arg_details.append(self._format_arg_line(arg_name, arg_type, default_value))

        return arg_details

    def _generate_verification_code(self) -> str:
        """Generates a verification code in the format XXXXXX-free-XXXXXXX or XXXXXX-paid-XXXXXX.

        Returns:
            str: MD5 hash of the generated verification code.
        """
        # Generate random numbers for the code
        prefix = ''.join([str(randint(0, 9)) for _ in range(6)])
        suffix = ''.join([str(randint(0, 9)) for _ in range(7)])

        # Create the code string
        code = f"{prefix}-{self.license_type}-{suffix}"

        # Return the MD5 hash of the code
        return hashlib.md5(code.encode('utf-8')).hexdigest()

    def _get_type_annotation(self, annotation_node: Optional[ast.AST]) -> str:
        """Extracts type annotation as string."""
        if annotation_node is None:
            return "Any"
        return ast.unparse(annotation_node) if hasattr(ast, 'unparse') else self._format_annotation(
            annotation_node,
        )

    def _unparse_or_format(self, node: ast.AST) -> str:
        """Safely unparse or format an AST node."""
        if node is None:
            return "None"
        try:
            return ast.unparse(node) if hasattr(ast, 'unparse') else self._format_default(node)
        except Exception:
            return "..."

    def _format_arg_line(self, name: str, type_str: str, default: Optional[str]) -> str:
        """Formats a single argument line."""
        if default is not None:
            return f"{name}: {type_str} = {default}"
        return f"{name}: {type_str}"

    def _format_annotation(self, node: ast.AST) -> str:
        """Fallback annotation formatting."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Subscript):
            return f"{self._format_annotation(node.value)}[{self._format_annotation(node.slice)}]"
        elif isinstance(node, ast.Attribute):
            return f"{self._format_annotation(node.value)}.{node.attr}"
        return "Any"

    def _generate_metadata_block(self) -> str:
        """Generates a metadata block with verification information.

        Returns:
            str: Formatted metadata block for the help file.
        """
        metadata = {
            "generator": "HelpFileGen",
            "version": "1.0",
            "timestamp": int(datetime.now().strftime('%Y-%m-%d')),
            "verification": {
                "code": self._generate_verification_code(),
                "type": self.license_type,
                "algorithm": "md5",
            },
        }

        # Convert to YAML-like format that's harder to tamper with
        metadata_lines = [
            "<!-- METADATA_BEGIN -->",
            f"// generator: {metadata['generator']}",
            f"// version: {metadata['version']}",
            f"// timestamp: {metadata['timestamp']}",
            f"// verification_code: {metadata['verification']['code']}",
            f"// verification_type: {metadata['verification']['type']}",
            f"// verification_algorithm: {metadata['verification']['algorithm']}",
            "<!-- METADATA_END -->",
        ]

        return "\n".join(metadata_lines)

    def _format_default(self, node: ast.AST) -> str:
        """Fallback default value formatting with proper None checking."""
        if node is None:
            return "None"

        if isinstance(node, ast.Constant):
            return repr(node.value)
        elif isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._format_default(node.value)}.{node.attr}"
        elif isinstance(node, ast.List):
            return f"[{', '.join(self._format_default(e) for e in node.elts)}]"
        elif isinstance(node, ast.Tuple):
            return f"({', '.join(self._format_default(e) for e in node.elts)})"
        elif isinstance(node, ast.Dict):
            # Safely handle dictionary with None checks
            pairs = []
            for k, v in zip(node.keys, node.values):
                key_str = self._format_default(k) if k is not None else "None"
                val_str = self._format_default(v) if v is not None else "None"
                pairs.append(f"{key_str}: {val_str}")
            return f"{{{', '.join(pairs)}}}"
        return "..."

    def _get_function_signature(self, node: ast.FunctionDef) -> str:
        """Generates a complete signature string for a function node.

        Args:
            node (ast.FunctionDef): The function node.

        Returns:
            str: String representing the complete function signature.
        """
        args = [arg.arg for arg in node.args.args]
        vararg = f"*{node.args.vararg.arg}" if node.args.vararg else ""
        kwonlyargs = [arg.arg for arg in node.args.kwonlyargs]
        kwarg = f"**{node.args.kwarg.arg}" if node.args.kwarg else ""
        defaults = [ast.dump(d) for d in (node.args.defaults or [])]
        kw_defaults = [ast.dump(d) for d in (node.args.kw_defaults or []) if d is not None]

        # Build signature string
        sig_parts = []
        if args:
            sig_parts.append(', '.join(args))
        if vararg:
            sig_parts.append(vararg)
        if kwonlyargs:
            sig_parts.append('*')
            sig_parts.append(', '.join(kwonlyargs))
        if kwarg:
            sig_parts.append(kwarg)

        signature = f"({', '.join(sig_parts)})"

        # Include defaults if present
        if defaults:
            signature += f" defaults={defaults}"
        if kw_defaults:
            signature += f" kw_defaults={kw_defaults}"

        return signature

    def _calculate_node_checksum(self, node: ast.AST) -> str:
        """Calculates a checksum for an AST node that includes docstrings and signatures.

        Args:
            node (ast.AST): The AST node to checksum.

        Returns:
            str: SHA256 checksum of the node's relevant attributes.
        """
        if isinstance(node, ast.ClassDef):
            # For classes, include: name, docstring, bases, decorators, and method signatures
            content = f"ClassDef:{node.name}:{ast.get_docstring(node) or ''}:"
            content += f"bases:{','.join(ast.dump(base) for base in node.bases)}:"
            content += f"decorators:{','.join(ast.dump(dec) for dec in node.decorator_list)}:"
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    content += (f"|{item.name}:{self._get_function_signature(item)}:"
                                f"{ast.get_docstring(item) or ''}")
            return hashlib.sha256(content.encode('utf-8')).hexdigest()

        elif isinstance(node, ast.FunctionDef):
            content = f"FunctionDef:{node.name}:{ast.get_docstring(node) or ''}:"
            content += f"decorators:{','.join(ast.dump(dec) for dec in node.decorator_list)}:"
            content += f"signature:{self._get_function_signature(node)}"
            return hashlib.sha256(content.encode('utf-8')).hexdigest()

        return hashlib.sha256(ast.dump(node).encode('utf-8')).hexdigest()

    def _extract_help_from_docstring(self, docstring: Optional[str]) -> Optional[str]:
        """Extracts help content from the docstring if present.

        Args:
            docstring (str): The docstring of a function.

        Returns:
            str: Extracted help content or None.
        """
        if not docstring:
            return None
        return docstring.strip()

    def _generate_help_content(self) -> str:
        """Walks the AST of the module and generates help content with navigation links."""
        help_content = []
        class_methods = {}
        current_checksums = {}
        previous_checksums = self._load_checksums()
        needs_update = False

        with open(self.module_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=self.module_path)

        # First pass: collect checksums
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                node_name = f"{node.name}" if isinstance(
                    node, ast.FunctionDef,
                ) else f"class_{node.name}"
                current_checksums[node_name] = self._calculate_node_checksum(node)

        # Check if update is needed
        if set(current_checksums.keys()) != set(previous_checksums.keys()):
            needs_update = True
        else:
            for node_name, checksum in current_checksums.items():
                if previous_checksums.get(node_name) != checksum:
                    needs_update = True

        if not needs_update and os.path.exists(self.output_file):
            if self.module_path not in self._already_reported_up_to_date:
                if self.logger is not None:
                    self.to_logger(
                        f"No changes detected in module {self.module_path}, help file is up to "
                        f"date.",
                        "info",
                    )
                else:
                    print(
                        f"No changes detected in module {self.module_path}, help file is up to "
                        f"date.",
                        "info",
                    )
                self._already_reported_up_to_date.add(self.module_path)
            self._save_checksums(current_checksums)
            return ""

        # Add metadata block at the very top
        # ! help_content.append(self._generate_metadata_block())
        # ! help_content.append("\n")

        # Second pass: generate content with navigation
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_name = node.name
                class_methods[class_name] = []
                class_anchor = f"class-{class_name.lower()}"

                # Class header with HTML anchor
                help_content.append(f'<a id="{class_anchor}"></a>\n')
                help_content.append(f"## Class: `{class_name}`\n")

                # Collect method names for quick links
                method_links = []
                for child in node.body:
                    if isinstance(child, ast.FunctionDef) and child.name not in self.exclusion_list:
                        method_anchor = f"method-{class_name.lower()}-{child.name.lower()}"
                        method_links.append(f"[`{child.name}`](#{method_anchor})")

                # Add quick jump links section if methods exist
                if method_links:
                    help_content.append(f"### Quick Links:\n")
                    help_content.append(" | ".join(method_links))
                    help_content.append("\n")

                # Process methods
                for child in node.body:
                    if isinstance(child, ast.FunctionDef):
                        if child.name in self.exclusion_list:
                            continue

                        method_anchor = f"method-{class_name.lower()}-{child.name.lower()}"
                        help_entry = f'<a id="{method_anchor}"></a>\n'
                        help_entry += f"### Method: `{child.name}`\n"

                        if self.args_seperatly:
                            help_entry += "#### Arguments:\n"
                            arg_details = self._get_argument_details(child)
                            help_entry += "\n".join(f"- {arg}" for arg in arg_details)

                        help_text = self._extract_help_from_docstring(ast.get_docstring(child))
                        help_entry += f"\n#### Help:\n> {help_text}\n" if help_text else ("\n#### "
                                                                                          "Help:\n> No help provided.\n")

                        help_entry += "\n#### Usage:\n```python\n"
                        help_entry += self.generate_usage_code(
                            class_name, child.name, self._get_argument_details(child),
                        )
                        help_entry += "\n```\n"

                        help_entry += (f"\n\n[Back to {class_name}](#{class_anchor}) or [Classes]("
                                       f"#top)\n\n")

                        class_methods[class_name].append(help_entry)
                        help_content.append(help_entry)

            elif isinstance(node, ast.FunctionDef):
                if node.name in self.exclusion_list:
                    continue

                # Standalone function
                func_anchor = f"func-{node.name.lower()}"
                help_entry = f'<a id="{func_anchor}"></a>\n'
                help_entry += f"## Function: `{node.name}`\n"

                if self.args_seperatly:
                    help_entry += "### Arguments:\n"
                    arg_details = self._get_argument_details(node)
                    help_entry += "\n".join(f"- {arg}" for arg in arg_details)

                help_text = self._extract_help_from_docstring(ast.get_docstring(node))
                help_entry += f"\n### Help:\n> {help_text}\n" if help_text else ("\n### Help:\n> "
                                                                                 "No help "
                                                                                 "provided.\n")

                help_entry += "\n### Usage:\n```python\n"
                help_entry += self.generate_usage_code(
                    "Global", node.name, self._get_argument_details(node),
                )
                help_entry += "\n```\n"

                help_entry += "\n[Back to top](#top)\n\n"
                help_content.append(help_entry)

        # Add table of contents at the top if we have content
        if help_content:
            toc = ['<a id="top"></a>\n']
            toc.append("## Table of Contents\n")
            class_anchors = []

            # Collect all class anchors
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    class_anchor = f"class-{node.name.lower()}"
                    class_anchors.append(f"[`{node.name}`](#{class_anchor})")

            # Add class links to TOC
            if class_anchors:
                toc.append("### Classes:\n")
                toc.append(" | ".join(class_anchors))
                toc.append("\n")

            # Add standalone function links to TOC
            func_anchors = []
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name not in self.exclusion_list:
                    func_anchor = f"func-{node.name.lower()}"
                    func_anchors.append(f"[`{node.name}`](#{func_anchor})")

            if func_anchors:
                toc.append("### Functions:\n")
                toc.append(" | ".join(func_anchors))
                toc.append("\n")

            help_content.insert(0, "\n".join(toc))

        self._save_checksums(current_checksums)
        return "\n\n".join(help_content)

    @staticmethod
    def generate_usage_code(class_name, name, usage_args):
        class_obj = f"self.{class_name.lower()}_obj" if class_name != "Global" else ""
        usage_string = f"{class_obj} = {class_name}\n\n" if class_name != "Global" else ""

        # Filter out 'self' and clean arguments
        cleaned_args = []
        for arg in usage_args:
            parts = arg.split(':', 1)
            param_part = parts[0].strip()
            if param_part == "self":
                continue
            if len(parts) > 1 and '=' in parts[1]:
                default_part = parts[1].split('=', 1)[1].strip()
                param_part += f"={default_part}"
            cleaned_args.append(param_part)

        call_prefix = f"{class_obj}.{name}(" if class_name != "Global" and name != "__init__" \
            else f"{class_obj}("

        if not cleaned_args:
            usage_string += f"{call_prefix})"
        elif len(cleaned_args) == 1:
            usage_string += f"{call_prefix}{cleaned_args[0]})"
        else:
            usage_string += f"{call_prefix}\n"
            usage_string += ",\n".join(f"    {arg}" for arg in cleaned_args)
            usage_string += "\n)"

        return usage_string

    @staticmethod
    def _update_help_file_incrementally(new_content: str, out_path: str) -> None:
        """Updates the help file incrementally by only changing modified sections.

        Args:
            new_content (str): The newly generated help content.
            out_path (str): Path to the help file.
        """
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        if not os.path.exists(out_path):
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    def to_logger(self, message, brief_type, brief=None):
        if not message:
            return

        log_levels = {
            "notset": logging.NOTSET,
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "warn": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
            "fatal": logging.CRITICAL,
            }

        if self.project_logging:
            if brief_type in log_levels:
                log_method = getattr(self.logger, brief_type, self.logger.log)
                if brief is not None:
                    # this might be wrong previously did  not include the "level" argument but will be
                    # highlighted yellow if removed.
                    log_method(level=brief_type, msg=f"{message}: {brief}")
                else:
                    # this might be wrong previously did  not include the "level" argument but will be
                    # highlighted yellow if removed.
                    log_method(level=logging.NOTSET, msg=f"{message}")
            else:
                level = log_levels.get(brief_type, logging.NOTSET)
                if brief is not None:
                    self.logger.log(level, msg=f"{message}: {brief}")
                else:
                    self.logger.log(level, msg=f"{message}")

        if self.debug_to_console:
            if brief is not None:
                print(f"{message}: {brief}")
            else:
                print(f"{message}")

    def generate_help_file(self) -> None:

        if os.path.isdir(self.module_path):
            for filename in os.listdir(self.module_path):
                if filename.endswith(".py"):
                    full_path = os.path.join(self.module_path, filename)
                    output_file = os.path.join(
                        self.dest_dir if self.dest_dir else self.module_path,
                        filename.replace(".py", ".md"),
                    )
                    sub_generator = HelpFileGen(
                        module_path=full_path,
                        logger=self.logger,
                        output_file=output_file,
                        exclusion_list=self.exclusion_list,
                        overwrite=self.overwrite,
                        args_separately=self.args_seperatly,
                        dest_dir=self.dest_dir,
                        dir_struct=self.dir_struct,  # <-- add this
                        verbose=self.verbose,  # <-- add this
                        keep_log=self.keep_log,  # <-- add this
                        project_logging=self.project_logging,  # <-- add this
                        debug_to_console=self.debug_to_console  # <-- add this
                    )
                    sub_generator.generate_help_file()

        else:

            if not self.output_file:
                basename = os.path.basename(self.module_path)
                self.output_file = os.path.join(
                    self.dest_dir if self.dest_dir else os.path.dirname(self.module_path),
                    basename.replace(".py", ".md"),
                )

            help_content = self._generate_help_content()
            if help_content:
                self._update_help_file_incrementally(help_content, self.output_file)
                if self.logger is not None:
                    self.to_logger(f"Help file generated/updated: {self.output_file}", "info")
                else:
                    print(f"Help file generated/updated: {self.output_file}")
            # else:
            #    print(f"No content generated for: {self.module_path}")
