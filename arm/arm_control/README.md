# arm_control

팔 PC에서 JetCobot을 움직인다.

- 액션 서버: `/workcell/fill_basket` (`FillBasket`, arm_left·arm_right가 함께 씀), `/arm_ship/swap_basket` (`SwapBasket`)
- 발행: `/arm_left/state`, `/arm_right/state`, `/arm_ship/state` (`ArmState`)

arm_left와 arm_right가 동시에 PLACING이면 안 된다.
