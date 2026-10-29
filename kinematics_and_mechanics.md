# 3D Printer Kinematics, Mechanical Movements & Physics Engineering Reference

This reference provides the mathematical models and mechanical engineering principles required to analyze how G-code commands translate into physical machine movements, forces, vibrations, and polymer flow across any 3D printer architecture.

## 1. Kinematic Transformation Matrices

When Klipper parses a G-code move $\Delta \mathbf{p} = (\Delta X, \Delta Y, \Delta Z)$ at toolhead velocity $\mathbf{v} = (v_x, v_y, v_z)$ and acceleration $\mathbf{a} = (a_x, a_y, a_z)$, the motor displacements and velocities depend on the kinematic topology:

### A. Cartesian (Bed-Slinger / Moving Bridge)
$$\begin{bmatrix} \Delta M_x \\ \Delta M_y \\ \Delta M_z \end{bmatrix} = \begin{bmatrix} 1 & 0 & 0 \\ 0 & 1 & 0 \\ 0 & 0 & 1 \end{bmatrix} \begin{bmatrix} \Delta X \\ \Delta Y \\ \Delta Z \end{bmatrix}$$
- **Mechanical Characteristic**: Each axis is decoupled. On bed-slingers (e.g., Ender-3, Prusa MK4), the Y-axis carries the heated bed plus the growing mass of the print ($m_y(t) = m_{\text{bed}} + m_{\text{part}}(t)$), lowering the Y-axis natural frequency $\omega_n = \sqrt{k_y / m_y(t)}$ as tall/heavy prints progress.

### B. CoreXY (Stationary / Z-Drop Bed, Belted XY Gantry)
$$\begin{bmatrix} \Delta M_A \\ \Delta M_B \\ \Delta M_Z \end{bmatrix} = \begin{bmatrix} 1 & 1 & 0 \\ 1 & -1 & 0 \\ 0 & 0 & 1 \end{bmatrix} \begin{bmatrix} \Delta X \\ \Delta Y \\ \Delta Z \end{bmatrix}$$
- **Inverse**: $\Delta X = \frac{1}{2}(\Delta M_A + \Delta M_B)$, $\Delta Y = \frac{1}{2}(\Delta M_A - \Delta M_B)$.
- **Mechanical Characteristic**:
  - Pure X or pure Y movement requires **both** motors $A$ and $B$ to rotate simultaneously at equal speed ($v_A = v_B = v_x$), sharing the inertial load across two stationary frame-mounted motors.
  - A **45-degree diagonal move** ($\Delta X = \Delta Y$) stops Motor B completely ($\Delta M_B = 0$) while Motor A moves at $v_A = \sqrt{2} \cdot v_{\text{toolhead}}$ ($\approx 1.414 \times$ toolhead speed).
  - **Belt Tension Asymmetry Risk**: If Belt A tension $T_A \ne$ Belt B tension $T_B$, the X-bridge racks at an angle $\theta_{\text{skew}} \propto (T_A - T_B)$, causing parallelograms instead of squares (requiring physical belt frequency matching at e.g. 110 Hz + Klipper `[skew_correction]`).

### C. CoreXZ (Coupled Belted X-Z Gantry + Bed-Slinger Y, e.g., Creality Ender-3 V3, Voron Switchwire)
$$\begin{bmatrix} \Delta M_X \\ \Delta M_Y \\ \Delta M_Z \end{bmatrix} = \begin{bmatrix} 1 & 0 & 1 \\ 0 & 1 & 0 \\ 1 & 0 & -1 \end{bmatrix} \begin{bmatrix} \Delta X \\ \Delta Y \\ \Delta Z \end{bmatrix}$$
- **Mechanical Characteristic**:
  - Eliminates leadscrew Z-wobble and allows extreme Z-hop speeds (`max_z_velocity: 200-500 mm/s`).
  - **Crucial Engineering Coupling**: Any horizontal move along X ($\Delta X \ne 0, \Delta Z = 0$) requires **both** `stepper_x` and `stepper_z` to spin at identical velocity ($\Delta M_X = +\Delta X, \Delta M_Z = +\Delta X$). If `stepper_x` and `stepper_z` have mismatched belt tension, microstep phase lag, or unequal `rotation_distance`, the nozzle rises or dips as it moves across X!
  - Conversely, when `[bed_mesh]` compensates for a tilted bed during an X move ($\Delta X \ne 0, \Delta Z_{\text{mesh}} \ne 0$), one motor moves by $\Delta X + \Delta Z_{\text{mesh}}$ and the other by $\Delta X - \Delta Z_{\text{mesh}}$, creating unequal motor velocities during flat horizontal extrusions.

### D. Linear Delta (Three Vertical Towers A, B, C)
For tower $i \in \{A, B, C\}$ at column coordinates $(X_i, Y_i)$ and diagonal rod length $L$:
$$M_i(X, Y, Z) = Z + \sqrt{L^2 - (X - X_i)^2 - (Y - Y_i)^2}$$
- **Mechanical Characteristic**: Non-linear step rate per mm across the build plate; requires high MCU step rates and precise rod-length/radius calibration (`DELTA_CALIBRATE`).

## 2. Klipper Lookahead & Centripetal Cornering Physics

1. **Cornering Junction Velocity**:
   At a corner of deflection angle $\theta$ (where $\theta = 0^\circ$ is a straight line and $\theta = 90^\circ$ is a right angle), Klipper models a virtual circular arc of radius $R$ tangent to both segments such that a $90^\circ$ turn occurs at `square_corner_velocity` ($v_{\text{scv}}$):
   $$v_{\text{junction}}(\theta) = v_{\text{scv}} \cdot \sqrt{\frac{(\sqrt{2} - 1) \cdot \sin(\theta / 2)}{1 - \sin(\theta / 2)}}$$
   - Increasing `square_corner_velocity` from `5.0` to `8.0` mm/s increases cornering speed by $1.6\times$, reducing time spent accelerating/decelerating and reducing corner over-extrusion, **provided** the frame and Input Shaper can handle the instantaneous centripetal impulse $\Delta v = \sqrt{2} \cdot v_{\text{scv}}$.
2. **Trapezoidal vs. Triangular Move Profiles**:
   For a segment of length $L$ starting at $v_0$ and ending at $v_1$ with acceleration $a$:
   - Minimum distance to reach cruise speed $v_c$:
     $$d_{\text{min}} = \frac{v_c^2 - v_0^2}{2a} + \frac{v_c^2 - v_1^2}{2a}$$
   - If $L < d_{\text{min}}$, the move is **acceleration-limited (triangular)** and peaks at:
     $$v_{\text{peak}} = \sqrt{a \cdot L + \frac{v_0^2 + v_1^2}{2}}$$

## 3. Belt Dynamics, Stepper Torque & Vertical Fine Artifacts (VFAs)

1. **Vertical Fine Artifacts (VFAs) / Belt & Motor Ripple**:
   VFAs appear as periodic vertical lines on printed walls with a fixed spatial pitch $\lambda$ (mm):
   - **GT2 Belt Tooth Pitch**: $\lambda_{\text{belt}} = 2.0\text{ mm}$ (caused by chordal polygon effect as belt teeth wrap over small idlers or large motor pulleys, or toothed belt riding on smooth idlers).
   - **1.8° Stepper Motor Pole Pitch**: A 200-step motor has 50 rotor teeth (electrical cycles per rev).
     - With a **20T GT2 pulley** ($\text{rotation\_distance} = 40\text{ mm}$): $\lambda_{\text{motor}} = \frac{40\text{ mm}}{50} = 0.80\text{ mm}$ per electrical cycle ($0.20\text{ mm}$ per full step).
     - With a **36T GT2 pulley** ($\text{rotation\_distance} = 72\text{ mm}$, e.g. stock Creality K1/K1 Max): $\lambda_{\text{motor}} = \frac{72\text{ mm}}{50} = 1.44\text{ mm}$ per electrical cycle ($0.36\text{ mm}$ per full step), amplifying motor torque ripple on the part wall and cutting linear force by $20/36 = 55.6\%$!
   - **Resonant VFA Speed**: When printing at velocity $v$, a spatial pitch $\lambda$ excites the frame at frequency:
     $$f_{\text{excitation}} = \frac{v}{\lambda}$$
     If $f_{\text{excitation}}$ coincides with the toolhead/frame natural frequency $f_0$ (e.g., $f_0 = 60\text{ Hz}$ with $\lambda = 2.0\text{ mm}$ at $v = 120\text{ mm/s}$, or $\lambda = 0.8\text{ mm}$ at $v = 48\text{ mm/s}$), VFAs resonate strongly. Avoid cruising at $v = f_0 \cdot \lambda$.

2. **Stepper Motor Back-EMF & Inductance Limit**:
   At rotational speed $\omega$ (rad/s), a stepper motor with phase inductance $L_m$ and back-EMF constant $K_e$ has maximum inductive current rate:
   $$\frac{dI}{dt} = \frac{V_{\text{bus}} - K_e \omega - I R_m}{L_m}$$
   - Higher supply voltage $V_{\text{bus}}$ (24V vs 48V) and lower inductance $L_m$ (1.5–2.5 mH LDO/Moons motors) preserve torque at speeds $> 300\text{ mm/s}$.

## 4. Extrusion Rheology & Hotend Melt-Zone Physics

1. **Volumetric Flow Rate ($Q$)**:
   For line width $w$, layer height $h$, and print speed $v$:
   $$Q = \left[ (w - h) \cdot h + \pi \left(\frac{h}{2}\right)^2 \right] \cdot v \approx w \cdot h \cdot v \quad (\text{mm}^3/\text{s})$$
   Required filament feed speed:
   $$v_E = \frac{Q}{\frac{\pi}{4} D_{\text{filament}}^2} = \frac{Q}{2.4053\text{ mm}^2} \quad (\text{for } 1.75\text{ mm filament})$$
2. **Hagen-Poiseuille Nozzle Pressure Drop**:
   $$\Delta P = \frac{8 \mu(T, \dot{\gamma}) \cdot L_{\text{nozzle}} \cdot Q}{\pi R_{\text{nozzle}}^4}$$
   Because $\Delta P \propto R_{\text{nozzle}}^{-4}$, switching from a `0.4 mm` nozzle to a `0.6 mm` nozzle reduces backpressure by $(0.6/0.4)^4 = 5.06\times$, dramatically reducing extruder compression and lowering required `pressure_advance`.
3. **Compression & Pressure Advance Physics**:
   The filament column of length $L_{\text{tube}}$ and bulk modulus $E_{\text{eff}}$ between the extruder drive gears and the melt zone acts as a spring with compliance $C_f \propto L_{\text{tube}}$ and melt viscosity resistance $R_n$. This forms a first-order lag with time constant $\tau = R_n C_f = K_{\text{PA}}$. Klipper's Pressure Advance applies the inverse lead filter $v_E(t) = v_{\text{nom}}(t) + K_{\text{PA}} \dot{v}_{\text{nom}}(t)$.
