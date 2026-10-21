"""Extract PAGE literal assignments only; never import the product package."""
import ast
from pathlib import Path
import sys

source, destination = map(Path, sys.argv[1:3])
tree = ast.parse(source.read_text(encoding='utf-8'))
assignments = [node for node in tree.body if isinstance(node, ast.Assign)
    and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id == 'PAGE']
assert assignments
environment = {'__builtins__': {}}
exec(compile(ast.Module(body=assignments, type_ignores=[]), '<PAGE literals>', 'exec'), environment)
with destination.open('x', encoding='utf-8') as stream:
    stream.write(environment['PAGE'].split('<script>', 1)[1].split('</script>', 1)[0])
