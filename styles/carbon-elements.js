/* Carbon Web Components — React 없이 쓰는 공식 컴포넌트.
 *
 * 이 앱은 Flask + Jinja 서버 렌더링이라 @carbon/react 를 쓸 수 없다.
 * @carbon/web-components 는 표준 커스텀 엘리먼트라 어느 템플릿 엔진에서든
 * 그냥 태그로 쓴다.
 *
 * 필요한 것만 가져온다. 전체를 번들하면 1MB 가 넘는데, 이 화면에서 쓰는 것은
 * 아래 몇 개뿐이다.
 */
import '@carbon/web-components/es/components/button/index.js';
import '@carbon/web-components/es/components/text-input/index.js';
import '@carbon/web-components/es/components/select/index.js';
import '@carbon/web-components/es/components/checkbox/index.js';
import '@carbon/web-components/es/components/tag/index.js';
import '@carbon/web-components/es/components/notification/index.js';
import '@carbon/web-components/es/components/tooltip/index.js';
import '@carbon/web-components/es/components/skeleton-text/index.js';
import '@carbon/web-components/es/components/tabs/index.js';
import '@carbon/web-components/es/components/toggle/index.js';
