from pathlib import Path
from setuptools import find_packages, setup


BASE_DIR = Path(__file__).resolve().parent
README = (BASE_DIR / "README.md").read_text(encoding="utf-8")


requirements_file = BASE_DIR / "requirements.txt"
if requirements_file.exists():
    requirements = [
        line.strip()
        for line in requirements_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
else:
    requirements = []

setup(
    name="praecentor",
    version="0.1.0",
    description="Praecentor package",
    long_description=README,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "praecentor": ["assets/**/*", "assets/*"],
        "": ["assets/**/*", "assets/*"]
    },
    install_requires=requirements,
    python_requires=">=3.8",
    zip_safe=False,
    entry_points={
        "console_scripts": [
            "praecentor=praecentor.cli:main"
        ]
    }
)
