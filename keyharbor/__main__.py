"""Public entry point; retain the legacy module for existing installations."""
import runpy

if __name__ == '__main__':
    runpy.run_module('cooper_litellm', run_name='__main__')
