from setuptools import find_packages, setup

package_name = "nao_speech_to_text"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="David Mora",
    maintainer_email="d.mora@uniandes.edu.co",
    description="Head-touch controlled speech-to-text bridge for NAO dialogue.",
    license="Academic",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "speech_to_text_node = nao_speech_to_text.speech_to_text_node:main",
        ],
    },
)
