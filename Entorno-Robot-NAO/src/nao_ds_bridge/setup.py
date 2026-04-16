from setuptools import find_packages, setup

package_name = "nao_ds_bridge"

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
    description="HTTP bridge for DS-Visualizer robot feedback events.",
    license="Academic",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "http_feedback_bridge = nao_ds_bridge.http_feedback_bridge:main",
        ],
    },
)

