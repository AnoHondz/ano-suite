"""Feature labels must follow fitted preprocessing, never source column offsets."""
import numpy as np
import pytest
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from generate_ml_report import _top_logistic_features


def test_empty_middle_column_does_not_crash_or_shift_labels():
    names=['first','empty_middle','last']
    x=np.array([[1,np.nan,4],[2,np.nan,3],[3,np.nan,2],[4,np.nan,1]])
    model=Pipeline([('imputer',SimpleImputer(strategy='median')),('scaler',StandardScaler()),
                    ('model',LogisticRegression(random_state=1))]).fit(x,[0,0,1,1])
    transformed=list(model[:-1].get_feature_names_out(names))
    assert transformed==['first','last']
    report=_top_logistic_features(model.named_steps['model'],transformed)
    assert {r['feature'] for r in report}=={'first','last'}
    with pytest.raises(ValueError,match='after preprocessing'):
        _top_logistic_features(model.named_steps['model'],names)


def test_all_present_names_still_match_coefficients():
    model=LogisticRegression(random_state=1).fit([[1,4],[2,3],[3,2],[4,1]],[0,0,1,1])
    assert len(_top_logistic_features(model,['left','right']))==2
