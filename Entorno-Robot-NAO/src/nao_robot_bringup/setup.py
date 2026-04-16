from glob import glob
from setuptools import setup

package_name = "nao_robot_bringup"

setup(
    name=package_name,
    version="0.1.0",
    packages=[],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="David Mora",
    maintainer_email="d.mora@uniandes.edu.co",
    description="Bringup package for the NAO robot pedagogical feedback stack.",
    license="Academic",
)

