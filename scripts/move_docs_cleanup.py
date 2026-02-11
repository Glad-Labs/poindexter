"""
Documentation cleanup script to move all .md files (except root README.md) 
into the /docs folder with unique naming to prevent conflicts.
"""

import os
import shutil
from pathlib import Path

def move_markdown_files():
    """Move all markdown files (except root README.md) to docs folder."""
    
    # Get the repository root
    repo_root = Path.cwd()
    docs_dir = repo_root / "docs"
    
    # Create docs directory if it doesn't exist
    docs_dir.mkdir(exist_ok=True)
    
    # Find all .md files except the root README.md
    md_files = []
    
    # Walk through all directories
    for root, dirs, files in os.walk(repo_root):
        # Skip the docs directory itself
        if "docs" in root:
            continue
            
        for file in files:
            if file.endswith('.md') and not (root == str(repo_root) and file == 'README.md'):
                md_files.append(Path(root) / file)
    
    print(f"Found {len(md_files)} Markdown files to move.")
    
    # Move each file
    for md_file in md_files:
        # Create a unique filename by prefixing with the relative path
        # e.g., web/public-site/README.md -> web_public-site_README.md
        relative_path = md_file.relative_to(repo_root)
        unique_name = str(relative_path).replace('/', '_').replace('\\', '_')
        
        # Create destination path
        dest_path = docs_dir / unique_name
        
        # Handle potential name conflicts by adding a counter
        counter = 1
        original_dest = dest_path
        while dest_path.exists():
            name_without_ext = original_dest.stem
            ext = original_dest.suffix
            dest_path = docs_dir / f"{name_without_ext}_{counter}{ext}"
            counter += 1
        
        # Move the file
        shutil.move(str(md_file), str(dest_path))
        print(f"Moved '{md_file}' to '{dest_path}'")

# Execute the script
if __name__ == "__main__":
    move_markdown_files()