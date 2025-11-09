import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

# create figure
fig, ax = plt.subplots(figsize=(9, 6))
ax.set_xlim(0, 10)
ax.set_ylim(0, 10)
ax.axis("off")


def box(x, y, w, h, text, fontsize="medium", fc="white"):
    """Draw a rounded box with centered text."""
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.2",
            fc=fc,
            ec="black",
            lw=1.2,
        )
    )
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
    )


# device core blocks
box(4, 6, 2.3, 1.2, "C3 Mini\n(main.py)", fontsize="large", fc="#f5f5f5")
box(4, 4.5, 2.3, 1.2, "SensorManager\n+ SEN55")
box(4, 3, 2.3, 1.2, "MaintenanceHandler")
box(4, 1.5, 2.3, 1.2, "Uploader\n(sensor.community / pulse.eco)")

# left side: Wi-Fi setup
box(0.5, 5, 2.3, 1.2, "WiFi setup\n(AP + web form)")
ax.annotate(
    "",
    xy=(4, 6.6),
    xytext=(2.8, 5.6),
    arrowprops={"arrowstyle": "->", "lw": 1},
)
ax.annotate(
    "",
    xy=(2.8, 5.0),
    xytext=(4, 4.8),
    arrowprops={"arrowstyle": "->", "lw": 1},
)

# MQTT broker on top
box(4, 8.5, 2.3, 1.2, "MQTT Broker")
ax.annotate(
    "",
    xy=(5.15, 8.5),
    xytext=(5.15, 7.2),
    arrowprops={"arrowstyle": "->", "lw": 1},
)
ax.annotate(
    "",
    xy=(5.35, 7.2),
    xytext=(5.35, 8.5),
    arrowprops={"arrowstyle": "->", "lw": 1},
)

# right side: client/app/API
box(7.5, 6, 2.3, 1.2, "Android / API\nsubscriber")
ax.annotate(
    "",
    xy=(7.5, 6.6),
    xytext=(6.3, 6.6),
    arrowprops={"arrowstyle": "->", "lw": 1},
)

# bottom: OTA
box(4, 0.2, 2.3, 1.0, "boot.py\n(OTA-lite)")
ax.annotate(
    "",
    xy=(5.15, 1.5),
    xytext=(5.15, 1.2),
    arrowprops={"arrowstyle": "->", "lw": 1},
)

# title
ax.text(
    5,
    9.9,
    "C3 Mini Environmental Sensor – System Architecture",
    ha="center",
    va="center",
    fontsize="large",
    fontweight="bold",
)

plt.tight_layout()
plt.savefig("docs_architecture.png", dpi=200, bbox_inches="tight")
